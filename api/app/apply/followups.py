"""Follow ups of a sent application, as pure functions (owner, 2026-11-07, "build all" of the
career-ops analysis; SPEC-apply.md, "Follow ups"). No I/O and no clock: the caller reads the
dates and passes `today`, the person's own day.

The cadence, adapted from career-ops (`followup-cadence.mjs` and `modes/followup.md`, MIT,
NOTICE), kept to what the person does not get from the mail module:
- an applied posting the company has not answered: the first follow up FIRST_AFTER days after
  the application, the second EVERY days after the first, and never a third (`cold`);
- an applied posting the company has answered since the last touch: no follow up (`answered`):
  their mail waits for a reply, which is the mail module's;
- a posting in interview: one thank you note THANK_YOU_AFTER day after the move to interview,
  then nothing more (`done`).
A mail the person sent to the company after the day of the application counts as a follow up,
as one recorded with "I sent it" does; two on the same day count once.

The text rules of a draft (`check_text`): a subject on one line, a body of at most WORDS_MAX
words, and the owner's dash rule applied by code (`undash`), whatever the model wrote.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.documents import rules as doc_rules

FIRST_AFTER = 7
EVERY = 7
SILENT_MAX = 2
THANK_YOU_AFTER = 1
WORDS_MAX = 150
SUBJECT_MAX = 200

Status = Literal["applied", "interview"]
Kind = Literal["follow_up", "thank_you"]
Urgency = Literal["due", "waiting", "cold", "answered", "done"]

# The order of the list: what is due first, then what waits, then the ends.
_RANK: dict[Urgency, int] = {"due": 0, "waiting": 1, "answered": 2, "cold": 3, "done": 4}
_WORD = re.compile(r"[^\W_]+(?:['\u2019.-][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True)
class Track:
    """What the cadence of one posting reads. `sent`: the days of the follow ups recorded;
    `mail_out`, `mail_in`: the days of the person's mails to the company and of the company's
    answers (an acknowledgement of the application left out by the caller)."""

    status: Status
    applied_on: date
    interview_on: date | None = None
    sent: tuple[date, ...] = ()
    mail_out: tuple[date, ...] = ()
    mail_in: tuple[date, ...] = ()


@dataclass(frozen=True)
class Cadence:
    kind: Kind
    urgency: Urgency
    # The day the next follow up is due; None when the cadence asks for none.
    next_on: date | None
    # Follow ups counted since the start of the stage (the application, or the interview).
    count: int


def touches(track: Track, start: date) -> list[date]:
    """The days the person wrote to the company after `start`: the recorded follow ups on or
    after it, the mails after it (the application mail itself is on its day), once a day."""
    found = {day for day in track.sent if day >= start}
    found |= {day for day in track.mail_out if day > start}
    return sorted(found)


def cadence(track: Track, today: date) -> Cadence:
    if track.status == "interview":
        start = track.interview_on or track.applied_on
        count = len(touches(track, start))
        if count:
            return Cadence("thank_you", "done", None, count)
        due = start + timedelta(days=THANK_YOU_AFTER)
        return Cadence("thank_you", "due" if today >= due else "waiting", due, 0)
    sent = touches(track, track.applied_on)
    last = sent[-1] if sent else track.applied_on
    if any(day > track.applied_on and day >= last for day in track.mail_in):
        return Cadence("follow_up", "answered", None, len(sent))
    if len(sent) >= SILENT_MAX:
        return Cadence("follow_up", "cold", None, len(sent))
    due = last + timedelta(days=EVERY if sent else FIRST_AFTER)
    return Cadence("follow_up", "due" if today >= due else "waiting", due, len(sent))


def sort_key(cadence_: Cadence, applied_on: date) -> tuple[int, date, date]:
    """Due first, the longest due on top; then what waits, the soonest first; then the ends."""
    return (_RANK[cadence_.urgency], cadence_.next_on or date.max, applied_on)


def can_draft(cadence_: Cadence) -> bool:
    """A draft is offered while a follow up is due or waits: never a third one to a silent
    application, nor a second thank you."""
    return cadence_.urgency in ("due", "waiting")


# --- the text of a draft ----------------------------------------------------------------------


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def undash(text: str) -> str:
    """Each dash used as a separator (an em dash, an en dash, a spaced hyphen) becomes a comma
    by the documents module's rule; "full-stack" and "2019-2021" keep their hyphen."""
    found = doc_rules.separators(text)
    return doc_rules.apply_separators(text, found) if found else text


def check_text(subject: str, body: str) -> str | None:
    """Why a draft cannot be kept as it is, as a code, or None: `subject` (empty, on two lines
    or over SUBJECT_MAX characters), `empty`, `too_long` (over WORDS_MAX words)."""
    if not subject.strip() or "\n" in subject or "\r" in subject or len(subject) > SUBJECT_MAX:
        return "subject"
    if not body.strip():
        return "empty"
    if word_count(body) > WORDS_MAX:
        return "too_long"
    return None


def days(values: Iterable[date | None]) -> tuple[date, ...]:
    return tuple(sorted({value for value in values if value is not None}))
