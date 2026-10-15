"""Company caps and the pace per ATS host (SPEC-apply, "Guardrails", 5). A stub written by apply
A6 so the gate calls it in its place in the order; apply A11 owns this file from then on and
replaces `check` with the person's company caps, Full auto's one per company rule, its safety
ceiling and the pace per host."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.apply.models import Application

# What a refusal of `check` says to the person, by code (A11 fills it).
SENTENCES: dict[str, str] = {}


def check(db: Session, application: Application, now: datetime) -> str | None:
    """The code of the first cap the submission would pass, or None. The caller holds the
    posting's row lock and the person's apply lock. Until A11: None."""
    return None
