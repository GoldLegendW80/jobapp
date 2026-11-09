"""The posting reader (jobs J19, issue #764; SPEC-jobs.md, "Posting reader"): an agent reads each
posting's text and fills its location, pay, work mode, contract type, published date, company and
clean description. Owner, 2026-11-09: every source is read, during the sweep that stored the
posting, and every saved posting is read once, except the fields the person set by hand.

The agent `posting_reader` (prompts/reader.md) runs on the agents runtime as the ingestion agents
do: `internal` (started here only), bound in code to at most BATCH_MAX postings of one person
(`bind_postings`: the run's input holds their ids, and the binding only those that are still that
person's), `private_io` (its events hold tool names and sizes, never a value), on MODEL for both
kinds of credential. Its two tools (reader_tools.py, worker only) take an index into the binding,
never an id: `read_postings` returns the bound postings, their text bounded to TEXT_SHOWN_MAX,
and `save_posting_facts` checks and stores one posting's facts (`save`).

What `save` stores, under the posting's row lock:
- each value with a quote of the posting's text that states it, found in the text as the finder
  compares words (`_words`: case, accents, spacing and punctuation aside); a value whose quote is
  not there, or that fails its check (`_location`, `_published`, `_pay`, the enums), is dropped
  and counted, never stored;
- the clean description: the text cut from the first match of the agent's start words to the last
  match of its end words after it (`cut`), refused under CUT_MIN_CHARS; the cut becomes the
  posting text and its head the description, and the fit is computed again as a rescore does,
  a fit set by hand kept;
- never a field the person set by hand (`hand_set`: named in an `edit` event that is not the
  reader's own, VIA), never a company or location that would make the posting a duplicate of
  another of the person's, never another contract on a website whose postings have one by nature
  (the VIE website, B21);
- what changed as one `edit` event with `"via": "reader"`, and `read_at`.

The code readers (opportunities/pay.py, the finder's labels, `work_mode_of`) give the first values
when the posting is stored and stay when no reader runs. Two ways a posting is read:
- during its sweep (`SweepReader`, sweep.py `run_sweep`), once every source ended: batches run in
  the sweep's own job, SWEEP_READS_AT_ONCE at a time, within SWEEP_READING_MAX_S and the sweep's
  bound; what is not read by then stays for the backlog;
- by the backlog (`tick`, a cron of the agents queue): one batch of the postings not read yet, as
  its own run_agent job, BACKLOG_RUNS at a time for the whole app, for a person with a credential
  and no sweep running, at most ATTEMPTS_MAX runs per posting. A posting without text is never read.

Logs carry ids and counts, never a posting text, a quote or a value.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker, undefer

from app import queue as queues
from app.agents import credentials, definitions, runs
from app.agents.models import AgentRun, RunStatus
from app.jobs import service
from app.jobs.models import (
    ContractType,
    Job,
    JobEvent,
    JobEventKind,
    PayPeriod,
    Sweep,
    SweepStatus,
    WorkMode,
)
from app.opportunities import pay as pays

if TYPE_CHECKING:
    from app.agents.runner import RunContext
    from app.jobs.sweep import Plan, Stats

logger = logging.getLogger("app.jobs.reader")

NAME = "posting_reader"
READ = "read_postings"
SAVE = "save_posting_facts"
VIA = "reader"
MODEL = "claude-sonnet-5-5"
BATCH_MAX = 10
ATTEMPTS_MAX = 3
MAX_TURNS = 30
WALL_TIME_S = 600.0
TEXT_SHOWN_MAX = 15_000
LOCATION_MAX = 300
COMPANY_MAX = 200
QUOTE_MAX = 400
# The start and the end of a clean description are a few words each, so each match is specific.
CUT_WORDS = (3, 30)
# A cut shorter than this keeps too little of a posting to be its own text: it is refused.
CUT_MIN_CHARS = 300
DESCRIPTION_CHARS = 300  # sweep.DESCRIPTION_CHARS, as `_new_job` stores it
EARLIEST = date(2000, 1, 1)
# The day of the read in the time zone where it is latest, so a date of today anywhere is kept.
LATEST_OFFSET = timedelta(hours=14)
SWEEP_READS_AT_ONCE = 2
SWEEP_READING_MAX_S = 600.0
# The reading ends this long before the sweep's own bound, so the sweep ends by itself.
SWEEP_MARGIN_S = 60.0
BACKLOG_RUNS = 1
BACKLOG_SECOND = 40
PROMPT_PATH = Path(__file__).parent / "prompts" / "reader.md"
FIRST_MESSAGE = (
    "Read the postings with read_postings, save the facts of each one with save_posting_facts, "
    "then answer with one line."
)

READER_FIELDS = ("location", "work_mode", "contract_type", "published_on", "company", "pay")
SAVED_ONCE = "This posting is already saved in this run. Go on with the next one."
BUSY = "This posting is being changed. Leave it and go on with the next one."
GONE = "This posting no longer exists. Go on with the next one."
NO_INDEX = "No posting has this index. Use an index read_postings gave."


class ReaderInput(BaseModel):
    """The input of one run: the ids of its postings, never a text."""

    model_config = ConfigDict(extra="forbid")

    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=BATCH_MAX)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def _readable() -> sa.ColumnElement[bool]:
    return sa.and_(Job.posting_text.is_not(None), sa.func.btrim(Job.posting_text) != "")


def _unread() -> sa.ColumnElement[bool]:
    return sa.and_(Job.read_at.is_(None), Job.read_attempts < ATTEMPTS_MAX, _readable())


def bind_postings(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What a run reads: `{"jobs": [id, ...]}`, the postings of its input that are still the
    run's person's and hold a text, in the input's order. None when none is left: the run then
    fails before any model call."""
    try:
        wanted = list(dict.fromkeys(ReaderInput.model_validate(run.input or {}).job_ids))
    except ValidationError:
        return None
    held = set(
        db.scalars(
            sa.select(Job.id).where(Job.user_id == run.user_id, Job.id.in_(wanted), _readable())
        )
    )
    jobs = [str(job_id) for job_id in wanted if job_id in held]
    return {"jobs": jobs} if jobs else None


POSTING_READER = definitions.register(
    definitions.AgentDefinition(
        name=NAME,
        description="Reads each posting found and fills its location, pay, work mode, contract, "
        "published date, company and description from its text.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=(READ, SAVE),
        input_model=ReaderInput,
        model=MODEL,
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="low",
        default_message=FIRST_MESSAGE,
        internal=True,
        bind=bind_postings,
        private_io=True,
        live_text=False,
    )
)


# --- words, quotes and the cut ---------------------------------------------------------------


def _words(text: str) -> str:
    # The finder's folding, imported here: at the top it would register the finder's agents in
    # every worker at its start, and their day long wall time would become the agents queue's.
    from app.browser.finder import _words as words

    return words(text)


def _plain(ch: str) -> str:
    if ch.isascii():
        return ch.lower()
    return "".join(
        c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
    ).casefold()


def fold_map(text: str) -> tuple[str, list[int]]:
    """`_words(text)`, and for each of its characters the index in `text` of the character it
    comes from: what lets a match of folded words be cut out of the text itself."""
    out: list[str] = []
    origin: list[int] = []
    for at, ch in enumerate(text):
        for c in _plain(ch):
            if c.isalnum():
                out.append(c)
                origin.append(at)
            elif out and out[-1] != " ":
                out.append(" ")
                origin.append(at)
    if out and out[-1] == " ":
        out.pop()
        origin.pop()
    return "".join(out), origin


def quoted(quote: object, folded: str) -> bool:
    """Whether `quote` holds a word and its words are in the folded text, as a run of whole
    words."""
    if not isinstance(quote, str) or len(quote) > QUOTE_MAX:
        return False
    words = _words(quote)
    return bool(words) and f" {words} " in f" {folded} "


def cut(text: str, folded: str, origin: list[int], start: object, end: object) -> str | None:
    """The posting's own text: from the first match of `start` to the last match of `end` after
    it, closing punctuation kept. None when a match is missing, the words are too few or too
    many, or the cut keeps under CUT_MIN_CHARS."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    head, tail = _words(start), _words(end)
    least, most = CUT_WORDS
    if not all(least <= len(words.split()) <= most for words in (head, tail)):
        return None
    hay = f" {folded} "
    first = hay.find(f" {head} ")
    last = hay.rfind(f" {tail} ")
    if first < 0 or last < first:
        return None
    begin = origin[first]
    stop = origin[last + len(tail) - 1] + 1
    while stop < len(text) and not text[stop].isspace() and not text[stop].isalnum():
        stop += 1
    piece = text[begin:stop].strip()
    return piece if len(piece) >= CUT_MIN_CHARS else None


# --- the checks of each value ----------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Fact(_Strict):
    value: str = Field(min_length=1, max_length=LOCATION_MAX)
    quote: str = Field(min_length=1, max_length=QUOTE_MAX)


class _Mode(_Strict):
    value: Literal["remote", "hybrid", "office"]
    quote: str = Field(min_length=1, max_length=QUOTE_MAX)


class _Contract(_Strict):
    value: Literal["CDI", "CDD", "VIE", "Stage", "Alternance", "Freelance"]
    quote: str = Field(min_length=1, max_length=QUOTE_MAX)


class _Pay(_Strict):
    min: int
    max: int
    currency: str = Field(min_length=3, max_length=3)
    period: Literal["year", "month", "day", "hour"]
    quote: str = Field(min_length=1, max_length=QUOTE_MAX)


class _Cut(_Strict):
    start: str = Field(min_length=1, max_length=QUOTE_MAX)
    end: str = Field(min_length=1, max_length=QUOTE_MAX)


class Facts(_Strict):
    """What save_posting_facts takes: one posting's index and the facts its text states."""

    index: int = Field(ge=0, lt=BATCH_MAX)
    location: dict[str, Any] | None = None
    work_mode: dict[str, Any] | None = None
    contract_type: dict[str, Any] | None = None
    published_on: dict[str, Any] | None = None
    company: dict[str, Any] | None = None
    pay: dict[str, Any] | None = None
    description: dict[str, Any] | None = None


SAVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "minimum": 0, "maximum": BATCH_MAX - 1},
        **{
            name: {
                "type": "object",
                "properties": {"value": value, "quote": {"type": "string"}},
                "required": ["value", "quote"],
                "additionalProperties": False,
            }
            for name, value in (
                ("location", {"type": "string"}),
                ("work_mode", {"type": "string", "enum": [mode.value for mode in WorkMode]}),
                (
                    "contract_type",
                    {"type": "string", "enum": [kind.value for kind in ContractType]},
                ),
                ("published_on", {"type": "string", "description": "YYYY-MM-DD"}),
                ("company", {"type": "string"}),
            )
        },
        "pay": {
            "type": "object",
            "properties": {
                "min": {"type": "integer"},
                "max": {"type": "integer"},
                "currency": {"type": "string"},
                "period": {"type": "string", "enum": list(pays.PERIODS)},
                "quote": {"type": "string"},
            },
            "required": ["min", "max", "currency", "period", "quote"],
            "additionalProperties": False,
        },
        "description": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    "required": ["index"],
    "additionalProperties": False,
}

_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def _line(value: str, most: int) -> str | None:
    """One line of plain text, spaces collapsed, at most `most` characters; None otherwise."""
    if any(c in "<>" or unicodedata.category(c) in _REFUSED_CATEGORIES for c in value):
        return None
    text = " ".join(value.split())
    return text if 0 < len(text) <= most else None


def _published(value: str, now: datetime) -> date | None:
    try:
        found = date.fromisoformat(value)
    except ValueError:
        return None
    latest = (now.astimezone(UTC) + LATEST_OFFSET).date()
    if found.isoformat() != value or not EARLIEST <= found <= latest:
        return None
    return found


def _undash(text: str) -> str:
    from app.documents import rules as doc_rules

    found = doc_rules.separators(text)
    return doc_rules.apply_separators(text, found) if found else text


@dataclass
class Outcome:
    """What a save did with one posting's facts, by field name (never a value)."""

    stored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    by_hand: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # a duplicate's identity, a website's contract
    not_in_text: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def refused(self) -> int:
        return len(self.not_in_text) + len(self.invalid)

    def answer(self, index: int) -> str:
        parts = [f"Posting {index} saved."]
        for label, names in (
            ("Stored", self.stored),
            ("Already so", self.unchanged),
            ("Kept as the person set them by hand", self.by_hand),
            ("Kept as they are", self.kept),
            ("Dropped, the quote is not in the posting text", self.not_in_text),
            ("Dropped, not a valid value", self.invalid),
        ):
            if names:
                parts.append(f"{label}: {', '.join(names)}.")
        parts.append("Go on with the next posting.")
        return " ".join(parts)


def _checked(facts: Facts, folded: str, now: datetime, outcome: Outcome) -> dict[str, Any]:
    """The values that pass their checks and whose quote is in the text, by column."""
    values: dict[str, Any] = {}

    def grounded[M: BaseModel](name: str, model: type[M]) -> M | None:
        raw = getattr(facts, name)
        if raw is None:
            return None
        try:
            parsed = model.model_validate(raw)
        except ValidationError:
            outcome.invalid.append(name)
            return None
        if not quoted(getattr(parsed, "quote", None), folded):
            outcome.not_in_text.append(name)
            return None
        return parsed

    location = grounded("location", _Fact)
    if location is not None:
        line = _line(location.value, LOCATION_MAX)
        if line is None:
            outcome.invalid.append("location")
        else:
            values["location"] = _undash(line)
    company = grounded("company", _Fact)
    if company is not None:
        line = _line(company.value, COMPANY_MAX)
        if line is None:
            outcome.invalid.append("company")
        else:
            values["company"] = line
    mode = grounded("work_mode", _Mode)
    if mode is not None:
        values["work_mode"] = WorkMode(mode.value)
    contract = grounded("contract_type", _Contract)
    if contract is not None:
        values["contract_type"] = ContractType(contract.value)
    published = grounded("published_on", _Fact)
    if published is not None:
        day = _published(published.value, now)
        if day is None:
            outcome.invalid.append("published_on")
        else:
            values["published_on"] = day
    pay = grounded("pay", _Pay)
    if pay is not None:
        stated = pays.stated_pay(pay.min, pay.max, pay.currency, pay.period)
        if stated is None or not any(c.isdigit() for c in pay.quote):
            outcome.invalid.append("pay")
        else:
            values.update(
                pay_min=stated.min,
                pay_max=stated.max,
                pay_currency=stated.currency,
                pay_period=PayPeriod(stated.period),
            )
    return values


def hand_set(db: Session, job_id: uuid.UUID) -> set[str]:
    """The reader's fields the person set by hand: named in an `edit` event that is not the
    reader's own (Edit details writes them, service.edit_job), the four pay columns as `pay`."""
    names: set[str] = set()
    for data in db.scalars(
        sa.select(JobEvent.data).where(
            JobEvent.job_id == job_id, JobEvent.kind == JobEventKind.edit
        )
    ):
        if not isinstance(data, dict) or data.get("via") == VIA:
            continue
        changes = data.get("changes")
        if not isinstance(changes, dict):
            continue
        names.update("pay" if name in service.PAY_FIELDS else name for name in changes)
    return names


def _nature(job: Job) -> str | None:
    """The contract every posting of the job's website has by nature (B21), else None."""
    from app.browser.sources.interactive import nature_contract

    return nature_contract(job.url or "") or nature_contract(f"https://{job.source or ''}/")


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return service._jsonable(value)


# --- the tools' work -------------------------------------------------------------------------


def _ids(binding: Mapping[str, Any] | None) -> list[uuid.UUID]:
    return [uuid.UUID(str(one)) for one in (binding or {}).get("jobs", ())]


def postings(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    binding: Mapping[str, Any] | None,
    redact: Callable[[Any], Any],
) -> str:
    """What read_postings answers: the bound postings, by index, their text bounded."""
    ids = _ids(binding)
    with sessions() as db:
        found = {
            job.id: job
            for job in db.scalars(
                service.jobs_of(user_id).where(Job.id.in_(ids)).options(undefer(Job.posting_text))
            )
        }
        shown = []
        for index, job_id in enumerate(ids):
            job = found.get(job_id)
            if job is None:
                continue
            text = redact(job.posting_text or "")
            shown.append(
                {
                    "index": index,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "source": job.source,
                    "url": job.url,
                    "found_on": job.found_on.isoformat(),
                    "set_by_hand": sorted(hand_set(db, job_id) & set(READER_FIELDS)),
                    # Written by third parties: data for the model, never instructions.
                    "untrusted_posting_text": text[:TEXT_SHOWN_MAX],
                    "posting_text_truncated": len(text) > TEXT_SHOWN_MAX,
                }
            )
    return json.dumps({"postings": shown}, ensure_ascii=False)


@dataclass(frozen=True)
class Saved:
    text: str
    error: bool = False
    read: bool = False
    refused: int = 0


def save(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    binding: Mapping[str, Any] | None,
    raw: object,
    now: datetime,
) -> Saved:
    """What save_posting_facts does with one posting (module docstring), in one transaction under
    the posting's row lock. A malformed call stores nothing and says what to fix."""
    try:
        facts = Facts.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "facts"
        return Saved(f"Not saved: {where}: {first.get('msg', 'invalid')}. Fix it.", error=True)
    ids = _ids(binding)
    if facts.index >= len(ids):
        return Saved(NO_INDEX, error=True)
    with sessions() as db:
        try:
            job = service.lock_job(db, user_id, ids[facts.index])
        except service.JobBusyError:
            return Saved(BUSY, error=True)
        if job is None or not (job.posting_text or "").strip():
            return Saved(GONE, error=True)
        outcome = Outcome()
        text = job.posting_text or ""
        folded, origin = fold_map(text)
        values = _checked(facts, folded, now, outcome)
        hand = hand_set(db, job.id)
        _keep(db, job, values, hand, outcome)
        edits = _apply(job, values, outcome)
        if facts.description is not None:
            _describe(db, job, facts.description, text, folded, origin, edits, outcome, now)
        if edits:
            service.add_event(db, job, JobEventKind.edit, {"changes": edits, "via": VIA}, now)
            service.touch(job, now)
        db.flush()
        db.execute(
            sa.update(Job)
            .where(Job.id == job.id)
            .values(read_at=now, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    return Saved(outcome.answer(facts.index), read=True, refused=outcome.refused)


def _keep(db: Session, job: Job, values: dict[str, Any], hand: set[str], outcome: Outcome) -> None:
    """Take out of `values` what stays as it is: the fields set by hand, a company or location
    that would make the posting another of the person's, the contract of a website's nature."""
    for name in READER_FIELDS:
        columns = service.PAY_FIELDS if name == "pay" else (name,)
        if name in hand and any(column in values for column in columns):
            for column in columns:
                values.pop(column, None)
            outcome.by_hand.append(name)
    if "contract_type" in values and _nature(job) is not None:
        values.pop("contract_type")
        outcome.kept.append("contract_type")
    identity = [name for name in ("company", "location") if name in values]
    if identity and (
        service.find_duplicate(
            db,
            job.user_id,
            company=values.get("company", job.company),
            title=job.title,
            location=values.get("location", job.location),
            url=job.url,
            exclude=job.id,
        )
        is not None
    ):
        for name in identity:
            values.pop(name)
        outcome.kept.extend(identity)


def _apply(job: Job, values: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
    edits: dict[str, Any] = {}
    for column, value in values.items():
        before = getattr(job, column)
        if before == value:
            continue
        edits[column] = {"from": _jsonable(before), "to": _jsonable(value)}
        setattr(job, column, value)
    names = {"pay" if column in service.PAY_FIELDS else column for column in values}
    changed = {"pay" if column in service.PAY_FIELDS else column for column in edits}
    outcome.stored.extend(name for name in READER_FIELDS if name in changed)
    outcome.unchanged.extend(name for name in READER_FIELDS if name in names - changed)
    return edits


def _describe(
    db: Session,
    job: Job,
    raw: dict[str, Any],
    text: str,
    folded: str,
    origin: list[int],
    edits: dict[str, Any],
    outcome: Outcome,
    now: datetime,
) -> None:
    """The clean description: the cut becomes the posting text, its head the description, and
    the fit is computed again from it, a fit set by hand kept."""
    try:
        asked = _Cut.model_validate(raw)
    except ValidationError:
        outcome.invalid.append("description")
        return
    piece = cut(text, folded, origin, asked.start, asked.end)
    if piece is None:
        outcome.not_in_text.append("description")
        return
    if piece == text:
        outcome.unchanged.append("description")
        return
    edits["description"] = {"from_chars": len(text), "to_chars": len(piece)}
    job.posting_text = piece
    job.description = piece[:DESCRIPTION_CHARS] or None
    outcome.stored.append("description")
    service.read_text(job, now)
    skills = service.user_skills(db, job.user_id)
    if skills is None:
        return
    result = service.computed_score(job, skills, service.user_search(db, job.user_id))
    if result is not None:
        service.apply_score(db, job, result, now, via=VIA)


# --- starting runs ---------------------------------------------------------------------------


@dataclass
class Tally:
    read: int = 0
    refused: int = 0


# What the save tool of a run started here counted, by run id, while the run lasts.
_TALLIES: dict[uuid.UUID, Tally] = {}


def count(run_id: uuid.UUID | None, saved: Saved) -> None:
    """Add a save's counts to the tally of its run, when one was asked for (the sweep's)."""
    tally = _TALLIES.get(run_id) if run_id is not None else None
    if tally is not None:
        tally.read += int(saved.read)
        tally.refused += saved.refused


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


def begin(
    db: Session, user_id: uuid.UUID, ids: list[uuid.UUID], now: datetime, script: str | None
) -> tuple[uuid.UUID, int] | str:
    """A queued run of the postings of `ids` another transaction does not hold, each counted as
    an attempt: its id and how many postings it holds, or why none was made ("no_credential",
    "busy": the person's runs are at runs.ACTIVE_PER_USER, "none": every posting is held or
    read). The caller commits."""
    if credentials.get(db, user_id) is None:
        return "no_credential"
    held = list(
        db.scalars(
            sa.select(Job.id)
            .where(Job.user_id == user_id, Job.id.in_(ids), _unread())
            .order_by(Job.id)
            .with_for_update(key_share=True, skip_locked=True)
        )
    )
    if not held:
        return "none"
    data = ReaderInput(job_ids=held, script=script).model_dump(mode="json", exclude_none=True)
    try:
        run = runs.create_run(db, user_id, NAME, None, data, MODEL, now)
    except runs.ActiveRunsError:
        return "busy"
    db.execute(
        sa.update(Job)
        .where(Job.id.in_(held))
        .values(read_attempts=Job.read_attempts + 1, updated_at=Job.updated_at)
        .execution_options(synchronize_session=False)
    )
    return run.id, len(held)


def swept(db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID) -> list[uuid.UUID]:
    """The postings a sweep stored (their `sweep` event) that wait for a reading."""
    stored = sa.select(JobEvent.job_id).where(
        JobEvent.user_id == user_id,
        JobEvent.kind == JobEventKind.sweep,
        JobEvent.data["sweep_id"].astext == str(sweep_id),
    )
    return list(
        db.scalars(
            sa.select(Job.id)
            .where(Job.user_id == user_id, Job.id.in_(stored), _unread())
            .order_by(Job.created_at, Job.id)
        )
    )


@dataclass
class SweepReader:
    """The sweep's reading of the postings it stored (sweep.py `run_sweep`, after every source
    ended): batches of BATCH_MAX, `at_once` runs at a time in this job, until `reading_s` from
    its start or SWEEP_MARGIN_S before the sweep's `deadline` (monotonic). Without a credential
    it ends at once. Its counts go to `stats.reading`: read, refused (values dropped), failed
    (postings of a run that ended without saving them) and left (for the backlog)."""

    context: Callable[[], RunContext]
    script: str | None = None
    at_once: int = SWEEP_READS_AT_ONCE
    reading_s: float = SWEEP_READING_MAX_S

    async def __call__(self, plan: Plan, stats: Stats, deadline: float) -> None:
        context = self.context()
        user_id = plan.user_id
        ids = await asyncio.to_thread(
            _in_db, context.sessions, lambda db: swept(db, user_id, plan.sweep_id)
        )
        if not ids:
            return
        reading = stats.reading
        reading.update(read=0, refused=0, failed=0, left=len(ids))
        has_key = await asyncio.to_thread(
            _in_db, context.sessions, lambda db: credentials.get(db, user_id) is not None
        )
        if not has_key:
            reading["skipped"] = "no_credential"
            return
        batches = deque(ids[at : at + BATCH_MAX] for at in range(0, len(ids), BATCH_MAX))
        stopped: list[str] = []

        async def one() -> None:
            while batches and not stopped:
                why = await self._read(context, user_id, batches.popleft(), reading)
                if why:
                    stopped.append(why)

        left_s = min(self.reading_s, deadline - SWEEP_MARGIN_S - time.monotonic())
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(max(0.0, left_s)):
                await asyncio.gather(*(one() for _ in range(max(1, self.at_once))))
        reading["left"] = max(0, len(ids) - reading["read"] - reading["failed"])
        if stopped:
            reading["skipped"] = stopped[0]
        logger.info(
            "sweep postings read",
            extra={"sweep_id": str(plan.sweep_id), **{k: v for k, v in reading.items()}},
        )

    async def _read(
        self,
        context: RunContext,
        user_id: uuid.UUID,
        batch: list[uuid.UUID],
        reading: dict[str, Any],
    ) -> str | None:
        """One run of one batch, to its end; why no further run should start, else None."""
        from app.agents import runner  # the SDK: the worker's image only
        from app.apply.agents import _release

        script = self.script
        creating = asyncio.ensure_future(
            asyncio.to_thread(
                _in_db,
                context.sessions,
                lambda db: begin(db, user_id, batch, context.clock(), script),
            )
        )
        try:
            made = await asyncio.shield(creating)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                late = await asyncio.shield(creating)
                if not isinstance(late, str):
                    await _release(context, late[0])
            raise
        if isinstance(made, str):
            return None if made == "none" else made
        run_id, size = made
        tally = _TALLIES[run_id] = Tally()
        ended = False
        try:
            await runner.execute(context, run_id)
            ended = True
        except asyncio.CancelledError:
            await _release(context, run_id)
            raise
        except Exception as error:
            logger.warning(
                "a sweep reading run failed",
                extra={"run_id": str(run_id), "error_type": type(error).__name__},
            )
            ended = True
        finally:
            _TALLIES.pop(run_id, None)
            reading["read"] += tally.read
            reading["refused"] += tally.refused
            if ended:
                reading["failed"] += max(0, size - tally.read)
        return None


# --- the backlog -----------------------------------------------------------------------------


def _active(user_id: uuid.UUID | None = None) -> sa.Select[tuple[int]]:
    query = (
        sa.select(sa.func.count())
        .select_from(AgentRun)
        .where(AgentRun.agent == NAME, AgentRun.status.in_((RunStatus.queued, RunStatus.running)))
    )
    return query if user_id is None else query.where(AgentRun.user_id == user_id)


def start_backlog(db: Session, now: datetime) -> list[uuid.UUID]:
    """The runs of the backlog to queue now (module docstring): none while BACKLOG_RUNS of the
    reader are queued or running; else one batch of the person whose last reading run is the
    oldest, among those with a posting to read, a credential, no reading run and no sweep
    running. The caller commits, then queues them."""
    # One tick at a time decides, whatever the number of workers.
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(0x6A6F_6252_6561_64)))
    if (db.scalar(_active()) or 0) >= BACKLOG_RUNS:
        return []
    last = (
        sa.select(sa.func.max(AgentRun.created_at))
        .where(AgentRun.agent == NAME, AgentRun.user_id == Job.user_id)
        .scalar_subquery()
    )
    sweeping = sa.select(Sweep.user_id).where(Sweep.status == SweepStatus.running)
    people = db.execute(
        sa.select(Job.user_id, last.label("last"))
        .where(_unread(), Job.user_id.not_in(sweeping))
        .group_by(Job.user_id)
        .order_by(sa.nulls_first(last.asc()), Job.user_id)
    ).all()
    for person, _ in people:
        if (db.scalar(_active(person)) or 0) > 0:
            continue
        ids = list(
            db.scalars(
                sa.select(Job.id)
                .where(Job.user_id == person, _unread())
                .order_by(Job.created_at.desc(), Job.id)
                .limit(BATCH_MAX)
            )
        )
        made = begin(db, person, ids, now, None)
        if not isinstance(made, str):
            return [made[0]]
    return []


async def tick(ctx: dict[str, Any]) -> int:
    """The worker's cron (agents queue): queue the backlog's next run as a run_agent job."""
    redis = ctx.get("redis")
    if redis is None:
        return 0
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    started = await asyncio.to_thread(_in_db, sessions, lambda db: start_backlog(db, clock()))
    for run_id in started:
        try:
            await redis.enqueue_job(
                "run_agent",
                str(run_id),
                _job_id=queues.agent_run_job_id(run_id),
                _queue_name=queues.AGENTS,
            )
        except Exception as error:  # Redis gone between the commit and the queue
            logger.warning(
                "a posting reading run was not queued",
                extra={"run_id": str(run_id), "error_type": type(error).__name__},
            )
            await asyncio.to_thread(
                _in_db,
                sessions,
                lambda db, r=run_id: runs.fail_queued(db, r, runs.QUEUE_DOWN, clock()),
            )
    return len(started)


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools: importing them registers them with the runner.
    from app.jobs import reader_tools as _tools  # noqa: F401
