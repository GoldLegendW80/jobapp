"""Job title suggestions (owner, 2026-11-05: "I want an agent to find the best job titles
according to the profile"; question 3: "The job titles agent refreshes by itself after a profile
fill or a skills change, at most once a day"). SPEC-profile, "Job title suggestions".

The agent, `profile_titles` (app/profile/agents.py), reads a `Request` built here from the stored
profile: the fit summary (app/profile/fit_summary.py: skills, years, recent titles, languages,
seniority, industries, company types; never a name, a contact detail or an employer), the wanted
and unwanted titles, the places (so it answers in their language) and the never apply rules. It
submits at most SUGGESTIONS_MAX `{title, reason, language}`.

**The checks** (`check`), by code, before any title is stored, and again on every read against
the search as it is saved then (`view`), first failing reason wins:
- `cybersecurity`: the app's own rule (`jobs.never_apply.CYBER_RULE`, the matcher the sweeps and
  apply refuse cybersecurity roles with) on the title or its reason (hard rule 2; a reason that
  names security refuses its title too: the side the rule errs on);
- `dash`: a dash used as a separator in the title (`documents.rules.separators`, hard rule 1);
- `shape`: not plain words as a website search types them (`plain_terms.PLAIN`), longer than
  TITLE_MAX characters or TITLE_WORDS_MAX words;
- `language`: not a two letter code;
- `reason`: the reason line, its dashes made commas by the documents' rule, is empty, longer
  than REASON_MAX, or not plain (a line break, a control character, markdown, an address);
- `listed`: already a wanted title, case, accents and a plural ending aside (`never_apply.fold`);
- `unwanted`: an unwanted title catches it (`profile.matching`, as the search filters titles);
- `never_apply`: one of the person's never apply rules catches it (`jobs.never_apply`, as the
  sweeps and apply read them);
- `repeated`: the same title earlier in the answer;
- `full`: the wanted list holds TERMS_MAX terms, or SUGGESTIONS_MAX are kept already.
A search section that no longer validates cannot be checked against: nothing is suggested.

**Runs and their bounds** (`start`). A run needs a model credential and something to read (skills
or the titles of past roles: a CV read by Fill my profile gives both). Automatic runs (`tick`, a
worker cron) follow a skills save or an accepted Fill my profile item, at most one per person in
AUTO_EVERY, and only when what the agent would read changed since the stored answer (`basis`); a
trigger inside the day waits for the day to pass. "More ideas" asks again on a click, at most
ASKS_PER_DAY times a UTC day. One run at a time per person. A run's token budget is the ceiling of
`estimate()`.

**The store** (`profile_title_suggestions`, one row per person): the last kept suggestions with
their time, so a page that shows them costs no model call.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents import credentials, definitions, runs
from app.agents.models import AgentRun, ModelCredential, Proposal, ProposalStatus, RunStatus
from app.documents import rules as doc_rules
from app.ingestion import pricing
from app.ingestion.estimate import F, RunEstimate, text_tokens
from app.jobs.never_apply import CYBER_RULE, NeverApplyRule, fold, matching_rule
from app.profile import fit_summary, matching, plain_terms
from app.profile.models import Profile, ProfileRevision, TitleSuggestions
from app.profile.schemas import TERMS_MAX, SearchSection

NAME = "profile_titles"
MODEL = definitions.DEFAULT_MODEL
MODEL_API_KEY = "claude-sonnet-5-5"  # on an API key, as the documents writers
PROMPT_PATH = Path(__file__).parent / "prompts" / "titles.md"
MAX_TURNS = 3  # one submit, one more if the tool refused its shape, one closing line
WALL_TIME_S = 60.0
SUGGESTIONS_MAX = 8
TITLE_MAX = 80  # characters of a suggested title (a plain term of the list may hold 200)
TITLE_WORDS_MAX = 6
REASON_MAX = 120  # characters of its one line reason
AUTO_EVERY = timedelta(days=1)
# "More ideas" clicks a person may make in one UTC day: each is one run, about 3 cents on an API
# key at the low estimate.
ASKS_PER_DAY = 3
# A run asked longer ago than this and still not recorded reads as failed (the queue may have
# lost it; the agents reaper fails it later).
STALE_AFTER = timedelta(minutes=30)
TICK_MAX = 20  # people the tick starts a run for at once
REQUEST_MAX = 12_000  # characters of the first message's data

# What the person reads when the last ask gave nothing.
ERRORS = {
    "token_budget": "The suggestions stopped at their token limit. Try again.",
    "no_answer": "The assistant gave no titles this time. Try again.",
    "failed": "The suggestions could not be made. Try again in a minute.",
    "lost": "The suggestions did not come back. Try again.",
    "queue_down": runs.QUEUE_DOWN,
}

LANGUAGE = re.compile(r"[a-z]{2}")
_DASHES = frozenset("\u2010\u2012\u2013\u2014\u2015\u2212")
_NOT_PLAIN = re.compile(r"[\x00-\x1f\x7f-\x9f*_#`<>\[\]{}|\\~]|https?:|www\.", re.IGNORECASE)


# --- what the agent reads -------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """What one run reads, built from the stored profile in code. Held in the worker's memory
    while the run lasts (agents.py), never stored, never logged."""

    summary: Mapping[str, Any]
    wanted: tuple[str, ...]
    unwanted: tuple[str, ...]
    places: tuple[str, ...]
    never_apply: tuple[str, ...]
    earlier: tuple[str, ...] = ()
    search: SearchSection = field(default_factory=SearchSection, compare=False)

    def data(self) -> dict[str, Any]:
        return {
            "profile_summary": dict(self.summary),
            "wanted_titles": list(self.wanted),
            "unwanted_titles": list(self.unwanted),
            "places": list(self.places),
            "never_apply_rules": list(self.never_apply),
            "earlier_suggestions": list(self.earlier),
        }


def search_of(sections: Mapping[str, Any]) -> SearchSection | None:
    """The stored search section; an empty one when never saved, None when it no longer passes
    the profile's checks (nothing can then be checked against it)."""
    raw = sections.get("search")
    if raw is None:
        return SearchSection()
    try:
        return SearchSection.model_validate(raw)
    except (ValidationError, TypeError):
        return None


def readable(summary: Mapping[str, Any]) -> bool:
    """Whether the profile holds something the agent can suggest from."""
    return any(summary.get(key) for key in ("skills", "adjacent_skills", "recent_roles"))


def request_of(sections: Mapping[str, Any], earlier: Iterable[str] = ()) -> Request | None:
    search = search_of(sections)
    if search is None:
        return None
    words = lambda terms: tuple(plain_terms.typed_words(terms)[0])  # noqa: E731
    summary = fit_summary.summary(sections)
    found = Request(
        summary=summary,
        wanted=words(search.title_include),
        unwanted=words(search.title_exclude),
        places=words(search.location),
        never_apply=tuple(search.never_apply),
        earlier=tuple(earlier)[:SUGGESTIONS_MAX],
        search=search,
    )
    # A list cut from its end until the whole fits, as the summary itself is bounded.
    while len(json.dumps(found.data(), ensure_ascii=False)) > REQUEST_MAX and found.wanted:
        found = Request(**{**found.__dict__, "wanted": found.wanted[:-1]})
    return found


def basis(request: Request) -> str:
    """What the run read, as a SHA-256: the earlier suggestions aside."""
    data = {**request.data(), "earlier_suggestions": []}
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The angle brackets and their lookalikes, each as a JSON escape (apply's `_block` rule).
_BRACKETS = str.maketrans({c: f"\\u{ord(c):04x}" for c in "<>\uff1c\uff1e\ufe64\ufe65"})


def prompt_for(request: Request) -> str:
    """The first message: what to do in one line, then the data as one JSON block between tags,
    every angle bracket escaped so nothing inside can close it."""
    text = json.dumps(request.data(), ensure_ascii=False, indent=1).translate(_BRACKETS)
    return (
        f"Suggest at most {SUGGESTIONS_MAX} job titles for this person, then call submit_titles "
        f"once.\n\n<person>\n{text}\n</person>"
    )


# --- the checks -------------------------------------------------------------------------------


class Submitted(BaseModel):
    """One entry of `submit_titles`, as the model sends it: shape only, judged by `check`."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=2 * TITLE_MAX)
    reason: str = Field(default="", max_length=4 * REASON_MAX)
    language: str = Field(default="en", max_length=8)


class SubmittedTitles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titles: list[Submitted] = Field(max_length=2 * SUGGESTIONS_MAX)


@dataclass(frozen=True)
class Suggestion:
    title: str
    reason: str
    language: str

    def stored(self) -> dict[str, str]:
        return {"title": self.title, "reason": self.reason, "language": self.language}


def _clean(text: str) -> str:
    return " ".join(text.split())


def _same(a: str, b: str) -> bool:
    """One title, case, accents, spacing and a plural ending aside."""
    x, y = fold(a).strip(), fold(b).strip()
    return x == y or any(x == y + end or y == x + end for end in plain_terms.PLURALS)


def _reason(text: str) -> str | None:
    """The reason line with its dashes made commas, or None when it is not short and plain."""
    if "\n" in text.strip() or "\r" in text.strip():
        return None
    found = doc_rules.separators(text)
    line = _clean(doc_rules.apply_separators(text, found) if found else text)
    if not line or len(line) > REASON_MAX or _NOT_PLAIN.search(line):
        return None
    return line


def refusal(
    entry: Submitted, search: SearchSection, kept: list[Suggestion], wanted: tuple[str, ...]
) -> str | None:
    """Why this entry is not shown (module docstring), None when it passes."""
    title = _clean(entry.title)
    if matching_rule([CYBER_RULE], title, entry.reason) is not None:
        return "cybersecurity"
    if doc_rules.separators(title) or any(ch in _DASHES for ch in title):
        return "dash"
    words = title.split(" ")
    if (
        len(title) > TITLE_MAX
        or len(words) > TITLE_WORDS_MAX
        or plain_terms.PLAIN.fullmatch(title) is None
    ):
        return "shape"
    if LANGUAGE.fullmatch(entry.language) is None:
        return "language"
    if _reason(entry.reason) is None:
        return "reason"
    if any(_same(title, one) for one in wanted):
        return "listed"
    exclude = search.title_exclude
    unwanted = matching.compile_terms(list(exclude.terms), exclude.advanced_regex)
    if unwanted is not None and unwanted.search(title):
        return "unwanted"
    rules = [NeverApplyRule.parse(text) for text in search.never_apply]
    if matching_rule(rules, title) is not None:
        return "never_apply"
    if any(_same(title, one.title) for one in kept):
        return "repeated"
    if len(kept) >= SUGGESTIONS_MAX or len(search.title_include.terms) >= TERMS_MAX:
        return "full"
    return None


def check(
    entries: Iterable[Submitted], search: SearchSection
) -> tuple[list[Suggestion], list[tuple[str, str]]]:
    """The entries kept, in order, and the ones refused with their reason code."""
    wanted = tuple(plain_terms.typed_words(search.title_include)[0])
    kept: list[Suggestion] = []
    refused: list[tuple[str, str]] = []
    for entry in entries:
        why = refusal(entry, search, kept, wanted)
        if why is not None:
            refused.append((entry.title, why))
            continue
        kept.append(Suggestion(_clean(entry.title), _reason(entry.reason) or "", entry.language))
    return kept, refused


def _stored(raw: object) -> list[Submitted]:
    """The stored suggestions as entries; a malformed one is left out."""
    out: list[Submitted] = []
    for one in raw if isinstance(raw, list) else []:
        try:
            out.append(Submitted.model_validate(one))
        except ValidationError:
            continue
    return out


# --- the estimate -------------------------------------------------------------------------------

SUBMIT_TOKENS = 600  # one submit of eight titles with their reasons
TEXT_OUTPUT = 100  # the closing line of a turn
TURNS_LOW = 2


def estimate(request_chars: int = REQUEST_MAX // 3) -> RunEstimate:
    """One run, counted turn by turn as the documents writers are (documents/estimate.py):
    every turn sends the fixed context `F`, the prompt and the first message again, and the
    submit stays in the context of the turns after it. Low: a submit and a closing line; high:
    MAX_TURNS turns, two submits, the largest first message."""
    prompt = text_tokens(len(PROMPT_PATH.read_text(encoding="utf-8")))
    low_fixed = F + prompt + text_tokens(request_chars)
    high_fixed = F + prompt + text_tokens(REQUEST_MAX)
    low_input = TURNS_LOW * low_fixed + SUBMIT_TOKENS
    low_output = SUBMIT_TOKENS + TURNS_LOW * TEXT_OUTPUT
    high_input = MAX_TURNS * high_fixed + 3 * SUBMIT_TOKENS
    high_output = 2 * SUBMIT_TOKENS + MAX_TURNS * TEXT_OUTPUT
    high = high_input + high_output
    return RunEstimate(
        steps=1,
        reused=0,
        low=low_input + low_output,
        high=high,
        ceiling=2 * high,
        low_input=Fraction(low_input),
        low_fresh=low_fixed,
        low_output=low_output,
        high_input=Fraction(high_input),
        high_output=high_output,
    )


TOKEN_BUDGET = estimate().ceiling


def estimate_out(kind: object) -> dict[str, Any]:
    """The figures shown before "More ideas": tokens, and money on an API key only."""
    found = estimate()
    out: dict[str, Any] = {"tokens": {"low": found.low, "high": found.high}, "cost": None}
    if pricing.priced(kind):  # type: ignore[arg-type]
        costs = pricing.costs(found, MODEL_API_KEY)
        out["cost"] = {"currency": pricing.CURRENCY, "low": costs.low, "high": costs.high}
    return out


# --- the store ----------------------------------------------------------------------------------


def _row(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> TitleSuggestions:
    """The person's row, made when missing; locked FOR UPDATE when asked."""
    db.execute(insert(TitleSuggestions).values(user_id=user_id).on_conflict_do_nothing())
    query = sa.select(TitleSuggestions).where(TitleSuggestions.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return db.execute(query).scalar_one()


def _sections(db: Session, user_id: uuid.UUID) -> Mapping[str, Any]:
    found = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    return found if isinstance(found, Mapping) else {}


def _in_flight(db: Session, row: TitleSuggestions, now: datetime) -> bool:
    if row.run_id is None or row.asked_at is None or row.asked_at <= now - STALE_AFTER:
        return False
    status = db.scalar(sa.select(AgentRun.status).where(AgentRun.id == row.run_id))
    return status in (RunStatus.queued, RunStatus.running)


def _asks_today(row: TitleSuggestions, now: datetime) -> int:
    return row.asks if row.asks_on == _day(now) else 0


def _day(now: datetime) -> date:
    return now.astimezone(UTC).date()


class RefusedError(Exception):
    """A run that cannot start; `code` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def start(db: Session, user_id: uuid.UUID, now: datetime, *, auto: bool) -> uuid.UUID:
    """Create the person's next run, queued, under the lock of their row; the caller commits,
    then queues `agents.JOB`. Raises RefusedError: `running`, `no_credential`, `nothing_to_read`,
    `search_invalid`, `asked_enough` (a click past ASKS_PER_DAY), `not_due` (automatic: within
    AUTO_EVERY of the last one), `unchanged` (automatic: the same input as the stored answer),
    `busy` (the person's active runs, runs.ACTIVE_PER_USER)."""
    row = _row(db, user_id, lock=True)
    if _in_flight(db, row, now):
        raise RefusedError("running")
    if credentials.get(db, user_id) is None:
        raise RefusedError("no_credential")
    request = request_of(_sections(db, user_id))
    if request is None:
        raise RefusedError("search_invalid")
    if not readable(request.summary):
        raise RefusedError("nothing_to_read")
    if auto:
        if row.auto_at is not None and row.auto_at > now - AUTO_EVERY:
            raise RefusedError("not_due")
        if row.basis == basis(request):
            raise RefusedError("unchanged")
    elif _asks_today(row, now) >= ASKS_PER_DAY:
        raise RefusedError("asked_enough")
    data = {"request": uuid.uuid4().hex, "token_budget": TOKEN_BUDGET}
    try:
        made = runs.create_run(db, user_id, NAME, None, data, MODEL, now)
    except runs.ActiveRunsError:
        raise RefusedError("busy") from None
    row.run_id, row.asked_at, row.error = made.id, now, None
    if auto:
        row.auto_at = now
    else:
        row.asks = _asks_today(row, now) + 1
        row.asks_on = _day(now)
    db.flush()
    return made.id


def not_queued(db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> None:
    """The queue refused the run's job: the run fails, the person reads why, and a click is
    given back. The caller commits."""
    runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
    row = _row(db, user_id, lock=True)
    if row.run_id != run_id:
        return
    row.run_id, row.error = None, "queue_down"
    if row.auto_at == row.asked_at:
        row.auto_at = None
    elif row.asks_on == _day(now) and row.asks > 0:
        row.asks -= 1


def run_request(db: Session, run_id: uuid.UUID) -> tuple[str, Request] | None:
    """The key and the request of a queued run of this agent whose row still waits for it;
    None when there is nothing to run."""
    run = db.get(AgentRun, run_id)
    if run is None or run.agent != NAME or run.status is not RunStatus.queued:
        return None
    row = db.get(TitleSuggestions, run.user_id)
    if row is None or row.run_id != run_id:
        return None
    earlier = [str(one.get("title", "")) for one in row.suggestions or [] if isinstance(one, dict)]
    request = request_of(_sections(db, run.user_id), earlier)
    if request is None:
        return None
    return str((run.input or {}).get("request", "")), request


def finish(
    db: Session,
    run_id: uuid.UUID,
    request: Request | None,
    submitted: Iterable[Submitted],
    now: datetime,
) -> None:
    """Record the end of a run on its person's row: the kept suggestions of a run that ended
    done, or why it gave nothing. A row that asks for another run since is left alone."""
    run = db.get(AgentRun, run_id)
    if run is None:
        return
    row = _row(db, run.user_id, lock=True)
    if row.run_id != run_id:
        return
    row.run_id = None
    entries = list(submitted)
    if run.status is RunStatus.done and request is not None and entries:
        kept, _ = check(entries, request.search)
        row.suggestions = [one.stored() for one in kept]
        row.refreshed_at, row.basis, row.error = now, basis(request), None
    elif run.status is RunStatus.done:
        row.error = "no_answer"
    elif run.error == runs.QUEUE_DOWN:
        row.error = "queue_down"
    else:
        from app.agents.failures import TOKEN_LIMIT

        row.error = "token_budget" if run.error == TOKEN_LIMIT else "failed"
    db.flush()


def view(db: Session, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """What a page shows: the stored suggestions checked again against the search as saved now,
    whether a run is on its way or why the last one gave nothing, what is missing for a run,
    the clicks left today and the estimate."""
    row = db.get(TitleSuggestions, user_id)
    credential: ModelCredential | None = credentials.get(db, user_id)
    sections = _sections(db, user_id)
    search = search_of(sections)
    needs = None
    if credential is None:
        needs = "model_key"
    elif not readable(fit_summary.summary(sections)):
        needs = "cv"
    suggestions: list[dict[str, str]] = []
    status, error = "idle", None
    if row is not None:
        if search is not None:
            kept, _ = check(_stored(row.suggestions), search)
            suggestions = [one.stored() for one in kept]
        if row.run_id is not None:
            if _in_flight(db, row, now):
                status = "running"
            else:
                status, error = "failed", ERRORS["lost"]
        elif row.error is not None:
            status, error = "failed", ERRORS.get(row.error, ERRORS["failed"])
    return {
        "status": status,
        "needs": needs,
        "suggestions": suggestions,
        "refreshed_at": row.refreshed_at if row is not None else None,
        "error": error,
        "asks_left": max(0, ASKS_PER_DAY - (_asks_today(row, now) if row else 0)),
        "asks_per_day": ASKS_PER_DAY,
        "estimate": estimate_out(credential.kind) if credential is not None else None,
        "cost_note": (
            pricing.ESTIMATE_NOTE
            if credential is not None and pricing.priced(credential.kind)
            else None
        ),
    }


# --- the triggers -------------------------------------------------------------------------------


def due(db: Session, now: datetime, limit: int = TICK_MAX) -> list[uuid.UUID]:
    """The people whose skills were saved, or who accepted a Fill my profile item, since the
    tick last read their triggers, who hold a model credential and whose last automatic run is
    older than AUTO_EVERY (module docstring). Read only."""
    from app.ingestion import items as ingestion_items

    row = TitleSuggestions
    since = sa.func.coalesce(row.seen_at, sa.cast("-infinity", sa.DateTime(timezone=True)))
    skills_saved = (
        sa.exists()
        .where(
            ProfileRevision.user_id == Profile.user_id,
            ProfileRevision.section == "skills",
            ProfileRevision.created_at > since,
        )
        .correlate(Profile, row)
    )
    fill_accepted = (
        sa.exists()
        .where(
            Proposal.user_id == Profile.user_id,
            Proposal.target == ingestion_items.TARGET,
            Proposal.kind == ingestion_items.KIND,
            Proposal.status == ProposalStatus.accepted,
            Proposal.decided_at > since,
        )
        .correlate(Profile, row)
    )
    query = (
        sa.select(Profile.user_id)
        .join(ModelCredential, ModelCredential.user_id == Profile.user_id)
        .outerjoin(row, row.user_id == Profile.user_id)
        .where(sa.or_(row.auto_at.is_(None), row.auto_at <= now - AUTO_EVERY))
        .where(sa.or_(skills_saved, fill_accepted))
        .order_by(Profile.user_id)
        .limit(limit)
    )
    return list(db.scalars(query))


def seen(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    """The person's triggers up to `now` are handled (a run started, or none was needed)."""
    _row(db, user_id, lock=True).seen_at = now
    db.flush()


# Refusals of an automatic start after which its trigger is handled; any other (`not_due`,
# `running`, `busy`) leaves it for a later tick.
SETTLED = frozenset({"no_credential", "nothing_to_read", "search_invalid", "unchanged"})
