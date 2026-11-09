"""The "About you" document of each person (its skills, answers and facts): the text they
write, Done, what the app understood, Apply and Undo (SPEC-profile, "About you"; the parts, the
writer and the checks: about_text.py).

**The document** is made once, by code, from the profile the first time it is read (`_row`:
`about_text.convert`), so after the conversion the sections are unchanged until the person
applies something. It saves as the person types (`save_text`, a version per save: a stale one is
refused, two tabs). It is not a section: no revision, never exported.

**Done** on a part reads it. A fact part is read by code (`about_text.read_facts`: the block of
its heading and text) and applied at once, with Undo after it. The skills and the answers parts
are read by the agent `profile_interpret` (about_agent.py), as they were when Done was clicked,
against what the profile holds of them. That needs a model credential; a part with nothing in it
needs no model (its reading is that it states nothing). At most DONES_PER_DAY a UTC day, one at a
time. When the run ends (`finish`), code checks what it read (`about_text.interpret`) and keeps
the changes, the summary and the values not taken. Nothing of the profile changes then.

**Apply** (`apply`) writes each changed section through the profile's own save, one revision
each, so History and the rescore after a skills change work as for any save. It is refused when
the part was edited since Done, or when a section it writes changed since the reading (another
tab, Fill my profile, the classic editor): the person clicks Done again. What were switches in the
answers (the consents, the equal opportunity answers, a sponsorship, the salary) change only
here, each on its own line of the summary. **Undo** puts back what each section held before the
last Apply, while nothing has changed them since.

**In step.** Each part keeps the digest of its text and of what the profile held of it the last
time they agreed (`about_text.in_step`). A part edited since reads `edited`; a part whose fields
changed in the profile elsewhere reads `elsewhere`, and "Rewrite from my profile" (`rewrite`)
writes its text again from the sections.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents import credentials, runs
from app.agents.models import AgentRun, RunStatus
from app.ingestion import pricing
from app.ingestion.estimate import F, RunEstimate, text_tokens
from app.profile import about_text, service, titles
from app.profile.about_text import Interpretation, Part
from app.profile.models import ProfileAbout
from app.profile.schemas import SECTIONS, validate_section

NAME = "profile_interpret"
MODEL = titles.MODEL  # the documents writers' model for each kind of credential
MODEL_API_KEY = titles.MODEL_API_KEY
PROMPT_PATH = Path(__file__).parent / "prompts" / "interpret.md"
MAX_TURNS = 3  # one submit, one more if the tool refused its shape, one closing line
WALL_TIME_S = 120.0
# Done clicks that call the model, a person a UTC day. The owner has not decided this number.
DONES_PER_DAY = 30
STALE_AFTER = timedelta(minutes=30)  # a reading asked longer ago and not back reads as lost
_CONTROL = frozenset(chr(c) for c in [*range(0, 9), *range(11, 32), 127])

ERRORS = {
    "token_budget": "Reading this part stopped at its token limit. Try again.",
    "no_answer": "The assistant gave no reading of this part. Try again.",
    "failed": "This part could not be read. Try again in a minute.",
    "lost": "The reading of this part did not come back. Try again.",
    "queue_down": runs.QUEUE_DOWN,
}
NOTHING = "Nothing to change: your profile already says this."


class RefusedError(Exception):
    """A click that cannot go on; `code` says why (about_router.REFUSED)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleError(Exception):
    """The text was saved since the version the page sent."""


# --- what the agent reads -------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """What one reading sees: the part as Done sent it and what the profile holds of its kind.
    Held in the worker while the run lasts (about_agent.py), never stored, never logged."""

    kind: str
    heading: str
    body: str
    current: Any
    owned: tuple[str, ...] = ()
    sections: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def part(self) -> Part:
        return Part(self.heading, self.body)

    def data(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "heading": self.heading,
            "text": self.body,
            "current": self.current,
            "current_blocks": list(self.owned),
        }


_BRACKETS = titles._BRACKETS  # every angle bracket and lookalike, as JSON escapes


def prompt_for(request: Request) -> str:
    """One line saying what to do, then the data as one JSON block between tags, every angle
    bracket escaped so nothing inside can close it."""
    text = json.dumps(request.data(), ensure_ascii=False, indent=1).translate(_BRACKETS)
    return (
        "Read this part of the person's About you document, then call submit_interpretation "
        f"once.\n\n<part>\n{text}\n</part>"
    )


def _request(part: Part, sections: Mapping[str, Any], owned: list[str]) -> Request:
    return Request(
        kind=part.kind,
        heading=part.heading,
        body=part.body,
        current=about_text.held(part.kind, sections, owned),
        owned=tuple(owned),
        sections=sections,
    )


# --- the estimate ---------------------------------------------------------------------------------

TEXT_OUTPUT = 100  # the closing line of a turn
TURNS_LOW = 2


def estimate(request_chars: int, part_chars: int) -> RunEstimate:
    """One reading, counted as the job titles agent's (titles.estimate): every turn sends the
    fixed context, the prompt and the first message; the submit holds about what the part
    writes. Low: a submit and a closing line; high: three turns and two submits. The ceiling,
    twice the high, is the run's token budget."""
    prompt = text_tokens(len(PROMPT_PATH.read_text(encoding="utf-8")))
    fixed = F + prompt + text_tokens(request_chars)
    submit = text_tokens(part_chars) + 200
    low_input = TURNS_LOW * fixed + submit
    low_output = submit + TURNS_LOW * TEXT_OUTPUT
    high_input = MAX_TURNS * fixed + 3 * submit
    high_output = 2 * submit + MAX_TURNS * TEXT_OUTPUT
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


def _estimate_of(request: Request) -> RunEstimate:
    return estimate(len(prompt_for(request)), len(request.body))


def estimate_out(kind: object, found: RunEstimate) -> dict[str, Any]:
    """The figures shown before Done: tokens, and money on an API key only."""
    out: dict[str, Any] = {"tokens": {"low": found.low, "high": found.high}, "cost": None}
    if pricing.priced(kind):  # type: ignore[arg-type]
        costs = pricing.costs(found, MODEL_API_KEY)
        out["cost"] = {"currency": pricing.CURRENCY, "low": costs.low, "high": costs.high}
    return out


# --- the store ------------------------------------------------------------------------------------


def _digest(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sections(db: Session, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    profile = service.get_profile(db, user_id, now)
    return service.sections_view(profile)


def _stored(sections: Mapping[str, Any], name: str) -> dict[str, Any]:
    """A section as stored, a section never saved as its empty form."""
    found = sections.get(name)
    return validate_section(name, {}) if found is None else found


def _row(db: Session, user_id: uuid.UUID, now: datetime, *, lock: bool = False) -> ProfileAbout:
    """The person's document, written once from their profile when it does not exist yet."""
    found = db.get(ProfileAbout, user_id, with_for_update=lock, populate_existing=True)
    if found is not None:
        return found
    converted = about_text.convert(_sections(db, user_id, now))
    db.execute(
        insert(ProfileAbout)
        .values(
            user_id=user_id,
            text=converted.text,
            parts=converted.parts,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
    )
    query = sa.select(ProfileAbout).where(ProfileAbout.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return db.execute(query.execution_options(populate_existing=True)).scalar_one()


def _day(now: datetime) -> date:
    return now.date()


def _dones_today(row: ProfileAbout, now: datetime) -> int:
    return row.dones if row.dones_on == _day(now) else 0


def _running(row: ProfileAbout, now: datetime) -> bool:
    found = row.interpretation or {}
    if found.get("status") != "running":
        return False
    return datetime.fromisoformat(found["asked_at"]) > now - STALE_AFTER


def _parts(row: ProfileAbout) -> list[Part]:
    return about_text.split(row.text)[1]


def _find(row: ProfileAbout, heading: str) -> Part:
    wanted = about_text.key_of(heading)
    found = [part for part in _parts(row) if part.key == wanted]
    if not found:
        raise RefusedError("no_part")
    if len(found) > 1:
        raise RefusedError("duplicate")
    return found[0]


def _owned(row: ProfileAbout, part: Part) -> list[str]:
    return list((row.parts or {}).get(part.key, {}).get("blocks") or [])


def save_text(
    db: Session, user_id: uuid.UUID, version: int, text: str, now: datetime
) -> ProfileAbout:
    """Store the document as typed. Raises StaleError for another version, RefusedError
    `too_long` past about_text.TEXT_MAX and `not_text` for a control character."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > about_text.TEXT_MAX:
        raise RefusedError("too_long")
    if any(char in _CONTROL or "\ud800" <= char <= "\udfff" for char in text):
        raise RefusedError("not_text")
    row = _row(db, user_id, now, lock=True)
    if row.version != version:
        raise StaleError
    if row.text != text:
        row.text, row.version, row.updated_at = text, row.version + 1, now
    db.flush()
    return row


def start(
    db: Session, user_id: uuid.UUID, heading: str, version: int, now: datetime
) -> uuid.UUID | None:
    """Done on the part `heading` of the text at `version`: the run of its reading, queued (the
    caller commits, then queues about_agent.JOB), or None when code reads it here: a fact part,
    applied at once, or an empty part. Raises StaleError, and RefusedError `running`, `no_part`,
    `duplicate`, `too_long`, `no_credential`, `asked_enough`, `busy`."""
    row = _row(db, user_id, now, lock=True)
    if row.version != version:
        raise StaleError
    if _running(row, now):
        raise RefusedError("running")
    part = _find(row, heading)
    if len(part.text) > about_text.PART_MAX:
        raise RefusedError("too_long")
    asked = {
        "key": part.key,
        "heading": part.heading,
        "kind": part.kind,
        "body": part.body,
        "digest": part.digest,
        "asked_at": now.isoformat(),
    }
    if part.kind == "facts" or not part.body.strip():
        # Read by code: a fact block is its text as written (applied at once, Undo after it),
        # an empty part states nothing.
        row.interpretation = {**asked, "status": "running", "run_id": None}
        db.flush()
        empty = Interpretation(categories=[], gates=[], answers=about_text.AnswersIn())
        _record(row, _request(part, _sections(db, user_id, now), _owned(row, part)), empty, now)
        db.flush()  # apply reads the row again
        if part.kind == "facts" and (row.interpretation or {}).get("changes"):
            apply(db, user_id, now)
        return None
    if credentials.get(db, user_id) is None:
        raise RefusedError("no_credential")
    if _dones_today(row, now) >= DONES_PER_DAY:
        raise RefusedError("asked_enough")
    request = _request(part, _sections(db, user_id, now), _owned(row, part))
    data = {"request": uuid.uuid4().hex, "token_budget": _estimate_of(request).ceiling}
    try:
        made = runs.create_run(
            db,
            user_id,
            NAME,
            None,
            data,
            MODEL,
            now,
            flow=runs.WorkflowRef("about_you", trigger="about"),
        )
    except runs.ActiveRunsError:
        raise RefusedError("busy") from None
    row.interpretation = {**asked, "status": "running", "run_id": str(made.id)}
    row.run_id = made.id
    row.dones = _dones_today(row, now) + 1
    row.dones_on = _day(now)
    db.flush()
    return made.id


def not_queued(db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> None:
    """The queue refused the run's job: the run fails, the person reads why, the click is given
    back. The caller commits."""
    runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
    row = _row(db, user_id, now, lock=True)
    if row.run_id != run_id:
        return
    row.run_id = None
    row.interpretation = {**(row.interpretation or {}), "status": "failed", "error": "queue_down"}
    if row.dones_on == _day(now) and row.dones > 0:
        row.dones -= 1


def run_request(db: Session, run_id: uuid.UUID, now: datetime) -> tuple[str, Request] | None:
    """The key and the request of a queued reading its person still waits for; None else."""
    run = db.get(AgentRun, run_id)
    if run is None or run.agent != NAME or run.status is not RunStatus.queued:
        return None
    row = db.get(ProfileAbout, run.user_id)
    if row is None or row.run_id != run_id or not row.interpretation:
        return None
    asked = row.interpretation
    part = Part(asked["heading"], asked["body"])
    owned = list((row.parts or {}).get(asked["key"], {}).get("blocks") or [])
    request = _request(part, _sections(db, run.user_id, now), owned)
    return str((run.input or {}).get("request", "")), request


def _read(request: Request, given: Interpretation | None) -> about_text.Outcome | None:
    """What a part changes, checked: a fact part by code, skills and answers from the model."""
    if request.kind == "facts":
        return about_text.read_facts(request.part, request.sections, request.owned)
    if given is None:
        return None
    return about_text.interpret(request.part, request.sections, given)


def _record(
    row: ProfileAbout, request: Request, given: Interpretation | None, now: datetime
) -> None:
    """Check a reading against its part and keep what Apply will write."""
    asked = dict(row.interpretation or {})
    found = _read(request, given)
    if found is None:
        row.interpretation = {**asked, "status": "failed", "error": "no_answer"}
        return
    changes = {
        name: data
        for name, data in found.changes.items()
        if _stored(request.sections, name) != data
    }
    row.interpretation = {
        **asked,
        "status": "ready",
        "error": None,
        "read_at": now.isoformat(),
        "changes": changes,
        "base": {name: _digest(_stored(request.sections, name)) for name in changes},
        "lines": found.lines if changes else [],
        "refused": found.refused,
        "blocks": found.blocks,
    }
    if not changes and not found.refused:
        # Nothing to apply: the part and the profile agree.
        parts = dict(row.parts or {})
        parts[request.part.key] = about_text.in_step(request.part, request.sections, found.blocks)
        row.parts = parts


def finish(
    db: Session,
    run_id: uuid.UUID,
    request: Request | None,
    submitted: Interpretation | None,
    now: datetime,
) -> None:
    """Record the end of a reading on its person's row; a row that asked again since is left
    alone."""
    run = db.get(AgentRun, run_id)
    if run is None:
        return
    row = _row(db, run.user_id, now, lock=True)
    if row.run_id != run_id:
        return
    row.run_id = None
    if run.status is RunStatus.done and request is not None:
        _record(row, request, submitted, now)
    else:
        from app.agents.failures import TOKEN_LIMIT

        error = "token_budget" if run.error == TOKEN_LIMIT else "failed"
        if run.error == runs.QUEUE_DOWN:
            error = "queue_down"
        row.interpretation = {**(row.interpretation or {}), "status": "failed", "error": error}
    db.flush()


def apply(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    """Write what the last reading changes, one revision per section. Raises RefusedError
    `nothing`, `part_changed` (edited since Done), `profile_changed` (a section changed since
    the reading)."""
    row = _row(db, user_id, now, lock=True)
    found = row.interpretation or {}
    if found.get("status") != "ready" or not found.get("changes"):
        raise RefusedError("nothing")
    parts = {part.key: part for part in _parts(row)}
    part = parts.get(found["key"])
    if part is None or part.digest != found["digest"]:
        raise RefusedError("part_changed")
    profile = service.get_profile(db, user_id, now, lock=True)
    sections = service.sections_view(profile)
    if any(_digest(_stored(sections, name)) != digest for name, digest in found["base"].items()):
        raise RefusedError("profile_changed")
    before = {name: _stored(sections, name) for name in found["changes"]}
    for name in SECTIONS:
        if name in found["changes"]:
            profile = service.save_section(
                db, user_id, name, found["changes"][name], profile.revision, now
            )
    after = service.sections_view(profile)
    row.undo = {
        "key": part.key,
        "heading": part.heading,
        "lines": found["lines"],
        "before": before,
        "after": {name: _stored(after, name) for name in found["changes"]},
        "parts": row.parts,
    }
    states = dict(row.parts or {})
    states[part.key] = about_text.in_step(part, after, found.get("blocks") or [])
    row.parts = states
    row.interpretation = None
    row.updated_at = now
    db.flush()


def undo(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    """Put back what the last Apply replaced. Raises RefusedError `nothing`, `profile_changed`
    (a section it wrote changed since)."""
    row = _row(db, user_id, now, lock=True)
    found = row.undo
    if not found:
        raise RefusedError("nothing")
    profile = service.get_profile(db, user_id, now, lock=True)
    sections = service.sections_view(profile)
    if any(_stored(sections, name) != data for name, data in found["after"].items()):
        raise RefusedError("profile_changed")
    for name in SECTIONS:
        if name in found["before"]:
            data = found["before"][name]
            profile = service.save_section(db, user_id, name, data, profile.revision, now)
    row.parts = found["parts"]
    row.undo = None
    row.updated_at = now
    db.flush()


def discard(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    """Forget the last reading (its summary goes, nothing changes); a running one stays."""
    row = _row(db, user_id, now, lock=True)
    if not _running(row, now):
        row.interpretation = None
    db.flush()


def rewrite(
    db: Session, user_id: uuid.UUID, heading: str | None, version: int, now: datetime
) -> ProfileAbout:
    """Write a part's text again from the profile and mark it in step; with no heading, add a
    part for each fact block no part holds. Raises StaleError and RefusedError `no_part`,
    `duplicate`."""
    row = _row(db, user_id, now, lock=True)
    if row.version != version:
        raise StaleError
    sections = _sections(db, user_id, now)
    preamble, parts = about_text.split(row.text)
    states = dict(row.parts or {})
    if heading is None:
        held = {about_text.key_of(t) for s in states.values() for t in s.get("blocks") or []}
        answers = next((i for i, p in enumerate(parts) if p.kind == "answers"), len(parts))
        for block in about_text.blocks_of(sections):
            if about_text.key_of(block["title"]) in held:
                continue
            new = Part(about_text.block_heading(block["title"]), about_text.demote(block["body"]))
            if new.key in {part.key for part in parts}:
                continue
            parts.insert(answers, new)
            answers += 1
            states[new.key] = about_text.in_step(new, sections, [block["title"]])
    else:
        part = _find(row, heading)
        owned = _owned(row, part)
        new = Part(part.heading, about_text.body_of(part.kind, sections, owned))
        parts = [new if one.key == part.key else one for one in parts]
        states[new.key] = about_text.in_step(new, sections, owned)
    text = about_text.join(preamble, parts)
    if len(text) > about_text.TEXT_MAX:
        raise RefusedError("too_long")
    if text != row.text:
        row.text, row.version = text, row.version + 1
    row.parts, row.updated_at = states, now
    db.flush()
    return row


# --- what the page shows --------------------------------------------------------------------------


def _state(row: ProfileAbout, part: Part, sections: Mapping[str, Any]) -> str:
    stored = (row.parts or {}).get(part.key)
    if stored is None:
        return "new"
    if stored.get("basis") != about_text.basis(part.kind, sections, stored.get("blocks") or []):
        return "elsewhere"
    return "in_step" if stored.get("applied") == part.digest else "edited"


def view(db: Session, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """What the page shows; never calls a model."""
    row = _row(db, user_id, now)
    sections = _sections(db, user_id, now)
    credential = credentials.get(db, user_id)
    kind = None if credential is None else credential.kind
    parts = _parts(row)
    keys = [part.key for part in parts]
    shown = []
    for part in parts:
        request = _request(part, sections, _owned(row, part))
        shown.append(
            {
                "heading": part.heading,
                "key": part.key,
                "kind": part.kind,
                "state": _state(row, part, sections),
                "duplicate": keys.count(part.key) > 1,
                "too_long": len(part.text) > about_text.PART_MAX,
                "estimate": None
                if kind is None or part.kind == "facts"
                else estimate_out(kind, _estimate_of(request)),
            }
        )
    found = dict(row.interpretation or {})
    if found.get("status") == "running" and not _running(row, now):
        found = {**found, "status": "failed", "error": "lost"}
    reading = None
    if found:
        reading = {
            "heading": found["heading"],
            "key": found["key"],
            "status": found["status"],
            "error": ERRORS.get(found.get("error") or ""),
            "lines": found.get("lines") or ([] if found.get("changes") else [NOTHING]),
            "refused": found.get("refused") or [],
            "can_apply": bool(found.get("changes")),
            "stale": any(p.key == found["key"] and p.digest != found["digest"] for p in parts),
        }
        if found["status"] != "ready":
            reading["lines"] = []
    held = {about_text.key_of(t) for s in (row.parts or {}).values() for t in s.get("blocks") or []}
    missing = [
        b["title"]
        for b in about_text.blocks_of(sections)
        if about_text.key_of(b["title"]) not in held
    ]
    return {
        "text": row.text,
        "version": row.version,
        "updated_at": row.updated_at,
        "parts": shown,
        "reading": reading,
        "undo": None
        if not row.undo
        else {"heading": row.undo["heading"], "lines": row.undo["lines"]},
        "needs": "model_key" if credential is None else None,
        "advanced": about_text.advanced_count(sections),
        "missing_blocks": missing,
        "dones_left": max(0, DONES_PER_DAY - _dones_today(row, now)),
        "part_max": about_text.PART_MAX,
        "text_max": about_text.TEXT_MAX,
    }
