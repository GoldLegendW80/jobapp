"""Job title suggestions (owner, 2026-11-05: "I want an agent to find the best job titles
according to the profile"; question 3: "The job titles agent refreshes by itself after a profile
fill or a skills change, at most once a day"; the answers on the ideas: the agent "can read
everything to help the user find best jobs", and "More ideas" adds to the ideas shown, "add to
them"). SPEC-profile, "Job title suggestions".

The agent, `profile_titles` (app/profile/agents.py), reads a `Request` built here: the whole
profile and the documents' text (titles_input.py says what is read and what is left out), the
person's last MESSAGES_MAX messages to it, the wanted and unwanted titles, the places (so it
answers in their language), the never apply rules, the ideas shown now and the ones the person
said are not for them. It submits at most SUGGESTIONS_MAX `{title, reason, language}`.

**The checks** (`check`), by code, before any title is stored, and again on every read against
the search as it is saved then (`view`), first failing reason wins:
- `dash`: a dash used as a separator in the title (`documents.rules.separators`, hard rule 1);
- `shape`: not plain words as a website search types them (`plain_terms.PLAIN`), longer than
  TITLE_MAX characters or TITLE_WORDS_MAX words;
- `language`: not a two letter code;
- `reason`: the reason line, its dashes made commas by the documents' rule, is empty, longer
  than REASON_MAX, or not plain (a line break, a control character, markdown, an address);
- `listed`: already a wanted title, case, accents and a plural ending aside (`never_apply.fold`);
- `unwanted`: an unwanted title catches it (`profile.matching`, as the search filters titles);
- `never_apply`: one of the person's own never apply rules catches it (`jobs.never_apply`, as
  the sweeps and apply read them); the app has no rule of its own about a kind of role (owner,
  2026-11-05);
- `dismissed`: the person said it is not for them ("Not for me");
- `repeated`: the same title earlier in the answer, or among the ideas shown;
- `full`: the wanted list holds TERMS_MAX terms, or the answer gave SUGGESTIONS_MAX already.
A search section that no longer validates cannot be checked against: nothing is suggested.

**Runs and their bounds** (`start`). A run needs a model credential and something to read: a
profile with anything in it, a document's text, or a message. Automatic runs (`tick`, a worker
cron) follow a save of a section the agent reads, an accepted Fill my profile item, an upload or
a base CV saved (`due`), at most one per person in AUTO_EVERY, never while an upload is still
being read (READ_WAIT), and only when what the agent would read changed since the stored answer
(`basis`); a trigger inside the day waits for the day to pass. "More ideas", and a message sent
to the agent, ask again on a click, with no daily limit (owner, 2026-11-06: "I don't want day
limit for more ideas"); the router's guard per minute stops repeated clicks. One run at a time
per person. A run's token budget is the ceiling of `estimate()` for its first message.

**The store** (`profile_title_suggestions`, one row per person): the ideas kept, oldest first,
at most LIST_MAX (a run's new ideas join after them, the oldest go past the cap), with their
time, so a page that shows them costs no model call; the titles the person dismissed (the last
DISMISSED_MAX); the person's last messages.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
from app.jobs.never_apply import NeverApplyRule, fold, matching_rule
from app.profile import matching, plain_terms, titles_input
from app.profile.models import Profile, ProfileRevision, TitleSuggestions
from app.profile.schemas import TERMS_MAX, SearchSection

NAME = "profile_titles"
MODEL = definitions.DEFAULT_MODEL
MODEL_API_KEY = "claude-sonnet-5-5"  # on an API key, as the documents writers
PROMPT_PATH = Path(__file__).parent / "prompts" / "titles.md"
MAX_TURNS = 3  # one submit, one more if the tool refused its shape, one closing line
WALL_TIME_S = 60.0
SUGGESTIONS_MAX = 8  # titles kept from one answer
LIST_MAX = 20  # ideas kept on the row; past it the oldest go, the newest stay
DISMISSED_MAX = 30  # the last titles the person said are not for them
MESSAGES_MAX = 5  # the person's last messages to the agent
MESSAGE_MAX = 300  # characters of one message
TITLE_MAX = 80  # characters of a suggested title (a plain term of the list may hold 200)
TITLE_WORDS_MAX = 6
REASON_MAX = 120  # characters of its one line reason
AUTO_EVERY = timedelta(days=1)
# A run asked longer ago than this and still not recorded reads as failed (the queue may have
# lost it; the agents reaper fails it later).
STALE_AFTER = timedelta(minutes=30)
# An upload still waiting for its text, stored or claimed less than this ago, holds the automatic
# refresh so the run reads it; one stuck longer holds nothing.
READ_WAIT = timedelta(minutes=30)
TICK_MAX = 20  # people the tick starts a run for at once
REQUEST_MAX = 30_000  # characters of the first message
LISTS_MAX = 40  # terms of each search list, and never apply rules, the first message names
RULE_MAX = 300  # characters of one never apply rule there

# What the person reads when the last ask gave nothing.
ERRORS = {
    "token_budget": "The suggestions stopped at their token limit. Try again.",
    "no_answer": "The assistant gave no titles this time. Try again.",
    "failed": "The suggestions could not be made. Try again in a minute.",
    "lost": "The suggestions did not come back. Try again.",
    "queue_down": runs.QUEUE_DOWN,
}
# What the person reads when the last ask ended well but added nothing (`error` holds its code).
NOTES = {"nothing_new": "No new idea this time. The ideas you had stay."}

LANGUAGE = re.compile(r"[a-z]{2}")
_DASHES = frozenset("\u2010\u2012\u2013\u2014\u2015\u2212")
_NOT_PLAIN = re.compile(r"[\x00-\x1f\x7f-\x9f*_#`<>\[\]{}|\\~]|https?:|www\.", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


# --- what the agent reads -------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """What one run reads, built from the stored profile, documents and row in code. Held in the
    worker's memory while the run lasts (agents.py), never stored, never logged."""

    profile: Mapping[str, Any]
    documents: tuple[titles_input.DocumentText, ...]
    messages: tuple[str, ...]
    wanted: tuple[str, ...]
    unwanted: tuple[str, ...]
    places: tuple[str, ...]
    never_apply: tuple[str, ...]
    shown: tuple[str, ...] = ()
    dismissed: tuple[str, ...] = ()
    search: SearchSection = field(default_factory=SearchSection, compare=False)

    def data(self) -> dict[str, Any]:
        return {
            "profile": dict(self.profile),
            "documents": [one.data() for one in self.documents],
            "messages": list(self.messages),
            "wanted_titles": list(self.wanted),
            "unwanted_titles": list(self.unwanted),
            "places": list(self.places),
            "never_apply_rules": list(self.never_apply),
            "shown_suggestions": list(self.shown),
            "not_for_me": list(self.dismissed),
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


def readable(request: Request) -> bool:
    """Whether there is something to suggest from: anything in the profile, a document's text or
    a message (owner, 2026-11-05: any one of them is enough)."""
    return bool(
        request.profile
        or request.documents
        or request.messages
        or request.wanted
        or request.unwanted
        or request.places
        or request.never_apply
    )


# What is cut, last entry first, while the first message is longer than REQUEST_MAX: the
# documents first (each is capped already), the lists of the search after them.
_CUT = ("documents", "wanted", "unwanted", "places", "never_apply", "dismissed", "shown")


def request_of(
    sections: Mapping[str, Any],
    documents: Iterable[titles_input.DocumentText] = (),
    messages: Iterable[str] = (),
    shown: Iterable[str] = (),
    dismissed: Iterable[str] = (),
) -> Request | None:
    search = search_of(sections)
    if search is None:
        return None
    words = lambda terms: tuple(plain_terms.typed_words(terms)[0][:LISTS_MAX])  # noqa: E731
    found = Request(
        profile=titles_input.profile(sections),
        documents=tuple(documents),
        messages=tuple(messages)[-MESSAGES_MAX:],
        wanted=words(search.title_include),
        unwanted=words(search.title_exclude),
        places=words(search.location),
        never_apply=tuple(rule[:RULE_MAX] for rule in search.never_apply[:LISTS_MAX]),
        shown=tuple(shown)[-LIST_MAX:],
        dismissed=tuple(dismissed)[-DISMISSED_MAX:],
        search=search,
    )
    for name in _CUT:
        while len(prompt_for(found)) > REQUEST_MAX and getattr(found, name):
            found = replace(found, **{name: getattr(found, name)[:-1]})
    return found


def basis(request: Request) -> str:
    """What the run read, as a SHA-256: the ideas shown and the dismissed ones aside."""
    data = {**request.data(), "shown_suggestions": [], "not_for_me": []}
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The angle brackets and their lookalikes, each as a JSON escape (apply's `_block` rule).
_BRACKETS = str.maketrans({c: f"\\u{ord(c):04x}" for c in "<>\uff1c\uff1e\ufe64\ufe65"})


def prompt_for(request: Request) -> str:
    """The first message: what to do in one line, then the data as one JSON block between tags,
    every angle bracket escaped so nothing inside can close it."""
    text = json.dumps(request.data(), ensure_ascii=False, indent=1).translate(_BRACKETS)
    return (
        f"Suggest at most {SUGGESTIONS_MAX} new job titles for this person, then call "
        f"submit_titles once.\n\n<person>\n{text}\n</person>"
    )


def clean_message(text: str) -> str | None:
    """A message to the agent as it is kept: one line, its control characters and runs of
    spaces made one space; None when that leaves nothing or more than MESSAGE_MAX characters."""
    line = " ".join(_CONTROL.sub(" ", text).split())
    return line if 0 < len(line) <= MESSAGE_MAX else None


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


def _dashed(title: str) -> bool:
    return bool(doc_rules.separators(title)) or any(ch in _DASHES for ch in title)


def plain_title(text: str) -> str | None:
    """The title as plain words a website search types, its spaces made single; None when it
    is not one (a dash between words, a sign, over TITLE_MAX characters or TITLE_WORDS_MAX
    words)."""
    title = _clean(text)
    if (
        _dashed(title)
        or len(title) > TITLE_MAX
        or len(title.split(" ")) > TITLE_WORDS_MAX
        or plain_terms.PLAIN.fullmatch(title) is None
    ):
        return None
    return title


def refusal(
    entry: Submitted,
    search: SearchSection,
    kept: list[Suggestion],
    wanted: tuple[str, ...],
    before: tuple[str, ...] = (),
    dismissed: tuple[str, ...] = (),
    cap: int = SUGGESTIONS_MAX,
) -> str | None:
    """Why this entry is not shown (module docstring), None when it passes."""
    title = _clean(entry.title)
    if _dashed(title):
        return "dash"
    if plain_title(title) is None:
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
    if any(_same(title, one) for one in dismissed):
        return "dismissed"
    if any(_same(title, one.title) for one in kept) or any(_same(title, one) for one in before):
        return "repeated"
    if len(kept) >= cap or len(search.title_include.terms) >= TERMS_MAX:
        return "full"
    return None


def check(
    entries: Iterable[Submitted],
    search: SearchSection,
    *,
    before: Iterable[str] = (),
    dismissed: Iterable[str] = (),
    cap: int = SUGGESTIONS_MAX,
) -> tuple[list[Suggestion], list[tuple[str, str]]]:
    """The entries kept, in order, and the ones refused with their reason code. `before`: the
    ideas already shown, which an entry would repeat; `dismissed`: the person's "Not for me";
    `cap`: how many are kept at most (an answer's SUGGESTIONS_MAX, the row's LIST_MAX)."""
    wanted = tuple(plain_terms.typed_words(search.title_include)[0])
    shown, gone = tuple(before), tuple(dismissed)
    kept: list[Suggestion] = []
    refused: list[tuple[str, str]] = []
    for entry in entries:
        why = refusal(entry, search, kept, wanted, shown, gone, cap)
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
TYPICAL_CHARS = 6_000  # a first message without documents, for an estimate with no input read


def estimate(request_chars: int = TYPICAL_CHARS) -> RunEstimate:
    """One run on a first message of `request_chars` characters (REQUEST_MAX at most), counted
    turn by turn as the documents writers are (documents/estimate.py): every turn sends the
    fixed context `F`, the prompt and the first message again, and the submit stays in the
    context of the turns after it. Low: a submit and a closing line; high: MAX_TURNS turns and
    two submits. The ceiling, twice the high, is the run's token budget."""
    prompt = text_tokens(len(PROMPT_PATH.read_text(encoding="utf-8")))
    fixed = F + prompt + text_tokens(min(request_chars, REQUEST_MAX))
    low_input = TURNS_LOW * fixed + SUBMIT_TOKENS
    low_output = SUBMIT_TOKENS + TURNS_LOW * TEXT_OUTPUT
    high_input = MAX_TURNS * fixed + 3 * SUBMIT_TOKENS
    high_output = 2 * SUBMIT_TOKENS + MAX_TURNS * TEXT_OUTPUT
    high = high_input + high_output
    return RunEstimate(
        steps=1,
        reused=0,
        low=low_input + low_output,
        high=high,
        ceiling=2 * high,
        low_input=Fraction(low_input),
        low_fresh=fixed,
        low_output=low_output,
        high_input=Fraction(high_input),
        high_output=high_output,
    )


# The largest budget a run can get: the ceiling at REQUEST_MAX.
TOKEN_BUDGET = estimate(REQUEST_MAX).ceiling


def estimate_out(kind: object, request_chars: int = TYPICAL_CHARS) -> dict[str, Any]:
    """The figures shown before "More ideas": tokens, and money on an API key only."""
    found = estimate(request_chars)
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


def _messages(row: TitleSuggestions | None) -> list[dict[str, str]]:
    """The stored messages, oldest first; a malformed one is left out."""
    raw = row.messages if row is not None else []
    out = [
        {"text": one["text"], "at": str(one.get("at", ""))}
        for one in (raw if isinstance(raw, list) else [])
        if isinstance(one, dict) and isinstance(one.get("text"), str) and one["text"]
    ]
    return out[-MESSAGES_MAX:]


def _dismissed(row: TitleSuggestions | None) -> list[str]:
    raw = row.dismissed if row is not None else []
    return [one for one in raw if isinstance(one, str)] if isinstance(raw, list) else []


def _request(
    db: Session,
    user_id: uuid.UUID,
    row: TitleSuggestions | None,
    messages: list[dict[str, str]] | None = None,
) -> Request | None:
    """What a run of the person would read now (`messages`: these instead of the stored ones)."""
    shown = [one.title for one in _stored(row.suggestions if row is not None else [])]
    said = _messages(row) if messages is None else messages
    return request_of(
        _sections(db, user_id),
        titles_input.documents(db, user_id),
        [one["text"] for one in said],
        shown,
        _dismissed(row),
    )


def _in_flight(db: Session, row: TitleSuggestions, now: datetime) -> bool:
    if row.run_id is None or row.asked_at is None or row.asked_at <= now - STALE_AFTER:
        return False
    status = db.scalar(sa.select(AgentRun.status).where(AgentRun.id == row.run_id))
    return status in (RunStatus.queued, RunStatus.running)


class RefusedError(Exception):
    """A run that cannot start; `code` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def start(
    db: Session, user_id: uuid.UUID, now: datetime, *, auto: bool, message: str | None = None
) -> uuid.UUID:
    """Create the person's next run, queued, under the lock of their row; the caller commits,
    then queues `agents.JOB`. `message` (a click only, cleaned by `clean_message`) joins the
    person's kept messages when the run starts. Raises RefusedError: `running`,
    `no_credential`, `nothing_to_read`, `search_invalid`, `not_due` (automatic: within
    AUTO_EVERY of the last one), `unchanged` (automatic: the same input as the stored answer),
    `busy` (the person's active runs, runs.ACTIVE_PER_USER)."""
    row = _row(db, user_id, lock=True)
    if _in_flight(db, row, now):
        raise RefusedError("running")
    if credentials.get(db, user_id) is None:
        raise RefusedError("no_credential")
    messages = _messages(row)
    if message is not None and not auto:
        messages = [*messages, {"text": message, "at": now.isoformat()}][-MESSAGES_MAX:]
    request = _request(db, user_id, row, messages)
    if request is None:
        raise RefusedError("search_invalid")
    if not readable(request):
        raise RefusedError("nothing_to_read")
    if auto:
        if row.auto_at is not None and row.auto_at > now - AUTO_EVERY:
            raise RefusedError("not_due")
        if row.basis == basis(request):
            raise RefusedError("unchanged")
    budget = estimate(len(prompt_for(request))).ceiling
    data = {"request": uuid.uuid4().hex, "token_budget": budget}
    try:
        made = runs.create_run(db, user_id, NAME, None, data, MODEL, now)
    except runs.ActiveRunsError:
        raise RefusedError("busy") from None
    row.run_id, row.asked_at, row.error = made.id, now, None
    row.messages = messages
    if auto:
        row.auto_at = now
    db.flush()
    return made.id


def not_queued(db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> None:
    """The queue refused the run's job: the run fails and the person reads why. The caller
    commits."""
    runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
    row = _row(db, user_id, lock=True)
    if row.run_id != run_id:
        return
    row.run_id, row.error = None, "queue_down"
    if row.auto_at == row.asked_at:
        row.auto_at = None


def run_request(db: Session, run_id: uuid.UUID) -> tuple[str, Request] | None:
    """The key and the request of a queued run of this agent whose row still waits for it;
    None when there is nothing to run."""
    run = db.get(AgentRun, run_id)
    if run is None or run.agent != NAME or run.status is not RunStatus.queued:
        return None
    row = db.get(TitleSuggestions, run.user_id)
    if row is None or row.run_id != run_id:
        return None
    request = _request(db, run.user_id, row)
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
    """Record the end of a run on its person's row: the ideas of a run that ended done join
    after the kept ones that still pass, the oldest going past LIST_MAX; or why it gave nothing.
    A row that asks for another run since is left alone."""
    run = db.get(AgentRun, run_id)
    if run is None:
        return
    row = _row(db, run.user_id, lock=True)
    if row.run_id != run_id:
        return
    row.run_id = None
    entries = list(submitted)
    if run.status is RunStatus.done and request is not None and entries:
        gone = _dismissed(row)
        current, _ = check(_stored(row.suggestions), request.search, dismissed=gone, cap=LIST_MAX)
        before = [one.title for one in current]
        new, _ = check(entries, request.search, before=before, dismissed=gone)
        row.suggestions = [one.stored() for one in [*current, *new][-LIST_MAX:]]
        row.refreshed_at, row.basis = now, basis(request)
        row.error = None if new else "nothing_new"
    elif run.status is RunStatus.done:
        row.error = "no_answer"
    elif run.error == runs.QUEUE_DOWN:
        row.error = "queue_down"
    else:
        from app.agents.failures import TOKEN_LIMIT

        row.error = "token_budget" if run.error == TOKEN_LIMIT else "failed"
    db.flush()


def dismiss(db: Session, user_id: uuid.UUID, title: str) -> bool:
    """ "Not for me": the title leaves the ideas and joins the person's dismissed titles (the
    last DISMISSED_MAX), so no later run shows it again. False, and nothing done, when the title
    is not plain words (`plain_title`). The caller commits."""
    clean = plain_title(title)
    if clean is None:
        return False
    row = _row(db, user_id, lock=True)
    row.suggestions = [
        one
        for one in row.suggestions or []
        if not (isinstance(one, dict) and _same(str(one.get("title", "")), clean))
    ]
    kept = [one for one in _dismissed(row) if not _same(one, clean)]
    row.dismissed = [*kept, clean][-DISMISSED_MAX:]
    db.flush()
    return True


def clear_messages(db: Session, user_id: uuid.UUID) -> None:
    """Forget what the person wrote to the agent: no later run reads it. The caller commits."""
    _row(db, user_id, lock=True).messages = []
    db.flush()


def view(db: Session, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """What a page shows: the stored suggestions checked again against the search as saved now,
    whether a run is on its way or why the last one gave nothing, what is missing for a run, the
    person's kept messages, the clicks left today and the estimate of what a run would read."""
    row = db.get(TitleSuggestions, user_id)
    credential: ModelCredential | None = credentials.get(db, user_id)
    request = _request(db, user_id, row) if credential is not None else None
    needs = None
    if credential is None:
        needs = "model_key"
    elif request is not None and not readable(request):
        needs = "input"
    search = search_of(_sections(db, user_id)) if request is None else request.search
    suggestions: list[dict[str, str]] = []
    status, error, note = "idle", None, None
    if row is not None:
        if search is not None:
            stored = _stored(row.suggestions)
            kept, _ = check(stored, search, dismissed=_dismissed(row), cap=LIST_MAX)
            suggestions = [one.stored() for one in kept]
        if row.run_id is not None:
            if _in_flight(db, row, now):
                status = "running"
            else:
                status, error = "failed", ERRORS["lost"]
        elif row.error in NOTES:
            note = NOTES[row.error]
        elif row.error is not None:
            status, error = "failed", ERRORS.get(row.error, ERRORS["failed"])
    size = len(prompt_for(request)) if request is not None else TYPICAL_CHARS
    return {
        "status": status,
        "needs": needs,
        "suggestions": suggestions,
        "refreshed_at": row.refreshed_at if row is not None else None,
        "error": error,
        "note": note,
        "messages": _messages(row),
        "message_max": MESSAGE_MAX,
        "estimate": estimate_out(credential.kind, size) if credential is not None else None,
        "cost_note": (
            pricing.ESTIMATE_NOTE
            if credential is not None and pricing.priced(credential.kind)
            else None
        ),
    }


# --- the triggers -------------------------------------------------------------------------------


def due(db: Session, now: datetime, limit: int = TICK_MAX) -> list[uuid.UUID]:
    """The people with a trigger since the tick last read theirs (a save of a section the agent
    reads, an accepted Fill my profile item, an upload, a base CV saved), who hold a model
    credential, whose last automatic run is older than AUTO_EVERY and none of whose uploads still
    waits for its text (READ_WAIT) (module docstring). Read only."""
    from app.documents.models import Document, Scope
    from app.ingestion import items as ingestion_items
    from app.ingestion.models import IngestionDocument, TextStatus

    row = TitleSuggestions
    since = sa.func.coalesce(row.seen_at, sa.cast("-infinity", sa.DateTime(timezone=True)))

    def exists(*where: Any) -> Any:
        return sa.exists().where(*where).correlate(Profile, row)

    section_saved = exists(
        ProfileRevision.user_id == Profile.user_id,
        ProfileRevision.section.in_(titles_input.SECTIONS),
        ProfileRevision.created_at > since,
    )
    fill_accepted = exists(
        Proposal.user_id == Profile.user_id,
        Proposal.target == ingestion_items.TARGET,
        Proposal.kind == ingestion_items.KIND,
        Proposal.status == ProposalStatus.accepted,
        Proposal.decided_at > since,
    )
    uploaded = exists(
        IngestionDocument.user_id == Profile.user_id, IngestionDocument.created_at > since
    )
    base_saved = exists(
        Document.user_id == Profile.user_id,
        Document.scope == Scope.base,
        Document.updated_at > since,
    )
    reading = exists(
        IngestionDocument.user_id == Profile.user_id,
        IngestionDocument.text_status == TextStatus.pending,
        IngestionDocument.pending_since > now - READ_WAIT,
    )
    query = (
        sa.select(Profile.user_id)
        .join(ModelCredential, ModelCredential.user_id == Profile.user_id)
        .outerjoin(row, row.user_id == Profile.user_id)
        .where(sa.or_(row.auto_at.is_(None), row.auto_at <= now - AUTO_EVERY))
        .where(sa.or_(section_saved, fill_accepted, uploaded, base_saved))
        .where(sa.not_(reading))
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
