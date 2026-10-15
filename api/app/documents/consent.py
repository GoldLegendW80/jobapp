"""The consent of "Automatic generation" (task D9; SPEC-documents.md, Flow step 2, "The spend").

The pattern of ingestion's consent (`app.ingestion.estimate_router`): one row of
`document_consents` per person and version of the text. The current consent is the row of
`VERSION`, the highest of `TEXTS`: a new text is a new entry, never an edit of an old one
(`tests/documents/test_consent.py` pins a SHA-256 of each version), and everyone is asked
again. A consent to an older version never counts for the new words.

`figures` is what the dialog shows beside the text: the estimated tokens of one posting (its CV
and its letter, with the person's own profile and default base CV), and of a typical week at
the threshold, from the postings of the last 30 days whose fit is at or above it, scaled to
seven days and rounded up. On an API key, the costs of both at list prices
(`app.ingestion.pricing`); on a subscription, tokens only.

Nothing here reads a text into a log.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents import credentials
from app.agents.models import CredentialKind
from app.documents import estimate, store
from app.documents.models import DocumentConsent
from app.ingestion import pricing
from app.ingestion.estimate import RunEstimate
from app.jobs.models import Job
from app.profile.models import Profile

# Every text a person was ever asked to accept, by version. Never edit the words of a version:
# ADD the next one, and everyone is asked again (module docstring).
TEXTS: dict[int, str] = {
    1: (
        "When automatic generation is on, the app sends your profile without your contact "
        "details (its facts, experience, skills and answers), your base CV, the pages of a CV "
        "you upload as pictures, and each job posting at or above your fit threshold to "
        "Anthropic, through your own Claude subscription or Anthropic API key. It does this on "
        "its own for each such posting, within the safety ceiling of the day, to write a CV and "
        "a letter. Deleting a document here removes it from this app. It does not delete what "
        "Anthropic has already received."
    ),
}
VERSION = max(TEXTS)
OUTDATED = "The consent text has changed. Read the current text and accept it."
LOOKBACK_DAYS = 30
WEEK_DAYS = 7
MODEL = "claude-opus-5-5"  # on a subscription, as the writers (writer_cv.MODEL)
MODEL_API_KEY = "claude-sonnet-5-5"


def text(version: int) -> str:
    """The words of `version`. `LookupError` for a version that never existed."""
    try:
        return TEXTS[version]
    except KeyError:
        raise LookupError(f"no consent text of version {version}") from None


def current(db: Session, user_id: uuid.UUID) -> DocumentConsent | None:
    return db.get(DocumentConsent, (user_id, VERSION), populate_existing=True)


def has_consented(db: Session, user_id: uuid.UUID) -> bool:
    return store.consented(db, user_id, VERSION)


def record(db: Session, user_id: uuid.UUID, at: datetime) -> DocumentConsent:
    """Accept the current text. Twice keeps the first date. The caller commits."""
    store.accept_consent(db, user_id, VERSION, at)
    found = current(db, user_id)
    if found is None:  # inserted above, or already there: only a concurrent account delete
        raise LookupError("the consent row is gone")
    return found


# --- the figures of the dialog ------------------------------------------------------------------


@dataclass(frozen=True)
class Figures:
    model: str | None  # None without a credential
    priced: bool  # an API key: the costs are shown
    min_fit: int
    postings_30_days: int  # at or above the threshold, found in the last 30 days
    week_postings: int
    posting: RunEstimate
    week: RunEstimate


def min_fit(db: Session, user_id: uuid.UUID) -> int:
    """The person's threshold (`search.documents.min_fit`, task D10), else the default."""
    from app.documents.writer_cv import setting

    stored: Any = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    found = ((stored.get("search") or {}).get("documents") or {}).get("min_fit")
    if isinstance(found, int) and not isinstance(found, bool) and 0 <= found <= 100:
        return found
    return int(setting("documents_min_fit_default"))


def postings_at_or_above(
    db: Session,
    user_id: uuid.UUID,
    threshold: int,
    last_day: date,
    lookback_days: int = LOOKBACK_DAYS,
) -> int:
    """The person's postings found in the last `lookback_days` days (30; `last_day` included, the
    UTC date of the clock, see `eligibility.found_day`) whose fit is at or above the threshold.
    A posting without a fit is never counted."""
    since = last_day - timedelta(days=lookback_days - 1)
    return int(
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(
                Job.user_id == user_id,
                Job.fit.is_not(None),
                Job.fit >= threshold,
                Job.found_on >= since,
                Job.found_on <= last_day,
            )
        )
        or 0
    )


def week_postings(count_30_days: int) -> int:
    """A typical week: the 30 days' count scaled to 7 days, rounded up."""
    return math.ceil(count_30_days * WEEK_DAYS / LOOKBACK_DAYS)


def model_for(kind: CredentialKind | None) -> str | None:
    if kind is None:
        return None
    return MODEL_API_KEY if kind is CredentialKind.anthropic_api_key else MODEL


def figures(db: Session, user_id: uuid.UUID, last_day: date) -> Figures:
    credential = credentials.get(db, user_id)
    kind = CredentialKind(credential.kind) if credential is not None else None
    threshold = min_fit(db, user_id)
    profile = estimate.profile_chars(db, user_id)
    base = estimate.base_chars(db, user_id, None)
    one = estimate.posting(profile, base, estimate.POSTING_DEFAULT_CHARS, **estimate.limits())
    count = postings_at_or_above(db, user_id, threshold, last_day)
    week = week_postings(count)
    return Figures(
        model=model_for(kind),
        priced=pricing.priced(kind),
        min_fit=threshold,
        postings_30_days=count,
        week_postings=week,
        posting=one,
        week=estimate.times(one, week),
    )
