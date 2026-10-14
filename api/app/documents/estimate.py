"""The token estimate of a writer run and its ceiling (task D9; SPEC-documents.md, "Limits of one
run", "The spend"). The formulas, their assumptions and two worked figures are written under
"Measurements" in tasks/documents/todo.md; every figure here can be checked by hand.

A writer run is counted turn by turn, as ingestion counts a step (`app.ingestion.estimate`):
every turn sends the fixed context again, and what a tool returned, or what the writer
submitted, stays in the context of every later turn.

- `F` (7,000 tokens) is ingestion's fixed context per turn (its assumption A11), `P` the tokens
  of the writer's prompt, both paid at every turn.
- `R` is the tokens of what the writer reads once: the profile sections it may read (`facts`,
  `experience`, `skills`, `answers`), the base CV, the posting, the draft. Characters become
  tokens with ingestion's `text_tokens` (3 characters a token, A12).
- `O` is the tokens of one submit: `O_low` for a typical document (`CONTENT_LOW_CHARS`, D2's CV
  fixtures are 2 to 4 KB of JSON), `O_high` for twice that (`CONTENT_HIGH_CHARS`).
- Low: `n = 3` turns (the reads, one submit, the closing line).
  `input = n*(F+P) + (n-1)*R + (n-2)*O_low`, `output = O_low + n*TEXT_OUTPUT`.
- High: `n = DOCUMENTS_GENERATION_MAX_TURNS` turns and `k = DOCUMENTS_MAX_SUBMITS` submits, each
  read and each submit sent again at every later turn:
  `input = n*(F+P) + (n-1)*(R + k*O_high)`, `output = k*O_high + n*TEXT_OUTPUT`.
- `low` and `high` are input plus output, rounded up once; the ceiling is `C = 2 * high`, the
  generation's `token_ceiling` (the run's `token_budget`, "Limits of one run").

A posting's estimate is the pair the tick starts together: the CV and then the letter, which
reads the same sources plus the new CV (`O_low` more to read). The letter writer's prompt does
not exist yet (task D8): the CV's prompt size stands for it.

The estimates are `app.ingestion.estimate.RunEstimate` values, so `app.ingestion.pricing.costs`
prices them as it prices an ingestion run. Pure but for the `*_chars` readers at the end, which
read sizes, never a text into a log.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.documents import store
from app.ingestion.estimate import F, RunEstimate, text_tokens
from app.jobs.models import Job
from app.profile.models import Profile

PROMPTS = Path(__file__).parent / "prompts"
SECTIONS = ("facts", "experience", "skills", "answers")  # what a writer may read
TURNS_LOW = 3
TEXT_OUTPUT = 500  # tokens of text a turn writes besides a submit
CONTENT_LOW_CHARS = 4_000
CONTENT_HIGH_CHARS = 8_000
POSTING_DEFAULT_CHARS = 6_000  # a posting whose text is not stored yet
TURNS_HIGH_DEFAULT = 10  # DOCUMENTS_GENERATION_MAX_TURNS
SUBMITS_HIGH_DEFAULT = 4  # DOCUMENTS_MAX_SUBMITS


def prompt_chars(name: str) -> int:
    """The characters of a writer's prompt file, 0 when it does not exist yet."""
    path = PROMPTS / f"{name}.md"
    return len(path.read_text(encoding="utf-8")) if path.exists() else 0


def writer(
    prompt: int,
    reads: int,
    *,
    turns: int = TURNS_HIGH_DEFAULT,
    submits: int = SUBMITS_HIGH_DEFAULT,
) -> RunEstimate:
    """One writer run whose prompt has `prompt` characters and which reads `reads` characters
    (module docstring)."""
    fixed = F + text_tokens(prompt)
    read = text_tokens(reads)
    o_low, o_high = text_tokens(CONTENT_LOW_CHARS), text_tokens(CONTENT_HIGH_CHARS)
    n_low, n_high = TURNS_LOW, max(turns, TURNS_LOW)
    low_input = n_low * fixed + (n_low - 1) * read + (n_low - 2) * o_low
    low_output = o_low + n_low * TEXT_OUTPUT
    high_input = n_high * fixed + (n_high - 1) * (read + submits * o_high)
    high_output = submits * o_high + n_high * TEXT_OUTPUT
    high = high_input + high_output
    return RunEstimate(
        steps=1,
        reused=0,
        low=low_input + low_output,
        high=high,
        ceiling=2 * high,
        low_input=Fraction(low_input),
        low_fresh=fixed + read + o_low,
        low_output=low_output,
        high_input=Fraction(high_input),
        high_output=high_output,
    )


def combine(estimates: Iterable[RunEstimate]) -> RunEstimate:
    """Several runs as one estimate (a posting's CV and letter, a week of postings)."""
    found = list(estimates)
    low_input = sum((e.low_input for e in found), Fraction(0))
    high_input = sum((e.high_input for e in found), Fraction(0))
    low_output = sum(e.low_output for e in found)
    high_output = sum(e.high_output for e in found)
    high = math.ceil(high_input + high_output)
    return RunEstimate(
        steps=sum(e.steps for e in found),
        reused=0,
        low=math.ceil(low_input + low_output),
        high=high,
        ceiling=2 * high,
        low_input=low_input,
        low_fresh=sum(e.low_fresh for e in found),
        low_output=low_output,
        high_input=high_input,
        high_output=high_output,
    )


def times(estimate: RunEstimate, count: int) -> RunEstimate:
    return combine([estimate] * count) if count > 0 else combine([])


def base_profile(profile: int, **limits: int) -> RunEstimate:
    """A base CV written from a profile of `profile` characters (its four readable sections)."""
    return writer(prompt_chars("base"), profile, **limits)


def posting(profile: int, base: int, posting_text: int, **limits: int) -> RunEstimate:
    """A posting's CV and letter: both read the profile, the base CV and the posting; the
    letter reads the new CV too."""
    reads = profile + base + posting_text
    cv = writer(prompt_chars("cv"), reads, **limits)
    letter_prompt = prompt_chars("letter") or prompt_chars("cv")
    letter = writer(letter_prompt, reads + CONTENT_LOW_CHARS, **limits)
    return combine([cv, letter])


def as_json(estimate: RunEstimate) -> dict[str, int]:
    """What a generation row stores (`document_generations.estimate`)."""
    return {"low": estimate.low, "high": estimate.high, "ceiling": estimate.ceiling}


# --- sizes, read from the rows ------------------------------------------------------------------


def _size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False))


def profile_chars(db: Session, user_id: uuid.UUID) -> int:
    """The characters of the profile sections a writer may read."""
    stored = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    return sum(_size(stored.get(name)) for name in SECTIONS)


def base_chars(db: Session, user_id: uuid.UUID, base_document_id: uuid.UUID | None) -> int:
    """The characters of a base CV's draft: the one named, else the default, else none."""
    found = (
        store.get(db, user_id, base_document_id)
        if base_document_id is not None
        else store.default_base(db, user_id)
    )
    return _size(found.draft) if found is not None else 0


def posting_chars(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> int:
    found = db.execute(
        sa.select(
            sa.func.coalesce(sa.func.char_length(Job.posting_text), 0),
            sa.func.coalesce(sa.func.char_length(Job.description), 0),
            sa.func.char_length(Job.title) + sa.func.char_length(Job.company),
        ).where(Job.user_id == user_id, Job.id == posting_id)
    ).one_or_none()
    if found is None:
        return POSTING_DEFAULT_CHARS
    text, description, header = found
    return (text or description or POSTING_DEFAULT_CHARS) + header


def limits(settings: Mapping[str, int] | None = None) -> dict[str, int]:
    """`turns` and `submits` from the module's settings, their defaults without them."""
    from app.documents.writer_cv import setting

    found = dict(settings or {})
    return {
        "turns": int(found.get("turns", setting("documents_generation_max_turns"))),
        "submits": int(found.get("submits", setting("documents_max_submits"))),
    }


def for_base(db: Session, user_id: uuid.UUID) -> RunEstimate:
    """The estimate of a base CV built from this person's profile now."""
    return base_profile(profile_chars(db, user_id), **limits())


def for_posting(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    base_document_id: uuid.UUID | None = None,
) -> RunEstimate:
    """The estimate of one posting's CV and letter, the pair D10's tick and "Write now" start."""
    return posting(
        profile_chars(db, user_id),
        base_chars(db, user_id, base_document_id),
        posting_chars(db, user_id, posting_id),
        **limits(),
    )
