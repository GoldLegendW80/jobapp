"""The consent of the documents writers (task D9, revised by D29; SPEC-documents.md, Flow step 2,
"Writing on a click").

The pattern of ingestion's consent (`app.ingestion.estimate_router`): one row of
`document_consents` per person and version of the text. The current consent is the row of
`VERSION`, the highest of `TEXTS`: a new text is a new entry, never an edit of an old one
(`tests/documents/test_consent.py` pins a SHA-256 of each version), and everyone is asked
again. A consent to an older version never counts for the new words.

Version 1 described D10's automatic generation; version 2 (D29) says that nothing is written
without the person's click, and that the hiring panel reads each document. Version 1 is kept for
the record of who accepted it.

`figures` is what the dialog shows beside the text: the estimated tokens of one posting (its CV
and its letter with their hiring panels, from the person's own profile and default base CV). On
an API key, its cost at list prices (`app.ingestion.pricing`); on a subscription, tokens only.

Nothing here reads a text into a log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents import credentials
from app.agents.models import CredentialKind
from app.documents import estimate, store
from app.documents.models import DocumentConsent
from app.ingestion import pricing
from app.ingestion.estimate import RunEstimate

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
    2: (
        "When you click Write, Apply or Like on a job posting, or upload a CV, the app sends "
        "your profile without your contact details (its facts, experience, skills and answers), "
        "your base CV, the pages of a CV you upload as pictures, and that job posting to "
        "Anthropic, through your own Claude subscription or Anthropic API key, to write a CV and "
        "a letter and to have a panel of reviewers read and improve them. Nothing is written "
        "without your click. Deleting a document here removes it from this app. It does not "
        "delete what Anthropic has already received."
    ),
}
VERSION = max(TEXTS)
OUTDATED = "The consent text has changed. Read the current text and accept it."
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
    posting: RunEstimate


def model_for(kind: CredentialKind | None) -> str | None:
    if kind is None:
        return None
    return MODEL_API_KEY if kind is CredentialKind.anthropic_api_key else MODEL


def figures(db: Session, user_id: uuid.UUID) -> Figures:
    credential = credentials.get(db, user_id)
    kind = CredentialKind(credential.kind) if credential is not None else None
    profile = estimate.profile_chars(db, user_id)
    base = estimate.base_chars(db, user_id, None)
    one = estimate.posting(profile, base, estimate.POSTING_DEFAULT_CHARS, **estimate.limits())
    return Figures(model=model_for(kind), priced=pricing.priced(kind), posting=one)
