"""Helpers of apply A11's tests: postings, applications, clicks and caps written straight to the
database (no fill), a fixed clock, and the person's search options. Every company is invented."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import caps
from app.apply.caps_models import CompanyCap
from app.apply.models import Application, ApplicationEvent, Mode, State

ASHBY = "https://jobs.ashbyhq.com/{org}/" + "0" * 8 + "-0000-4000-8000-{n:012d}"


class Fixed:
    """A clock that says what the test sets."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def posting(
    db: Session,
    user_id: uuid.UUID,
    *,
    company: str = "Example Robotics",
    title: str = "Robotics Engineer",
    url: str | None = None,
) -> uuid.UUID:
    return db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url) "
            "VALUES (:u, :s, :c, :t, :url) RETURNING id"
        ),
        {"u": user_id, "s": f"a11-{uuid.uuid4().hex[:12]}", "c": company, "t": title, "url": url},
    ).scalar_one()


def application(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    *,
    state: State = State.submitted,
    at: datetime | None = None,
    mode: Mode = Mode.review,
    ats: str = "ashby",
    form_url: str | None = None,
) -> Application:
    """An application in `state`; `at` is its `submitted_at` (the time of the click)."""
    found = Application(
        user_id=user_id,
        posting_id=posting_id,
        mode=mode,
        state=state,
        ats=ats,
        form_url=form_url or ASHBY.format(org="example-robotics", n=uuid.uuid4().int % 10**12),
        canonical_key=f"url:a11/{uuid.uuid4()}",
        submitted_at=at,
    )
    db.add(found)
    db.flush()
    return found


def clicked(
    db: Session,
    user_id: uuid.UUID,
    at: datetime,
    *,
    state: State = State.submitted,
    company: str = "Example Robotics",
    title: str = "Robotics Engineer",
    **rest: Any,
) -> Application:
    """Another posting of the person, applied to by the app at `at`."""
    where = posting(db, user_id, company=company, title=title)
    org = caps.company_key(company) or "none"
    rest.setdefault("form_url", ASHBY.format(org=org, n=uuid.uuid4().int % 10**12))
    return application(db, user_id, where, state=state, at=at, **rest)


def click(db: Session, found: Application, at: datetime) -> None:
    """The `click` event the submit lease writes before the click."""
    db.add(
        ApplicationEvent(
            application_id=found.id, user_id=found.user_id, kind="click", at=at, data={}
        )
    )
    db.flush()


def cap(
    db: Session,
    user_id: uuid.UUID,
    label: str = "Example Robotics",
    max_count: int = 1,
    window_days: int = 90,
    same_role_days: int | None = None,
) -> None:
    db.add(
        CompanyCap(
            user_id=user_id,
            company_key=caps.company_key(label),
            label=label,
            max_count=max_count,
            window_days=window_days,
            same_role_days=same_role_days,
        )
    )
    db.flush()


def search(db: Session, user_id: uuid.UUID, section: dict[str, Any]) -> None:
    """The person's search options, stored as the profile stores them."""
    db.execute(
        sa.text(
            "UPDATE profiles SET sections = sections || jsonb_build_object('search', "
            "CAST(:s AS jsonb)) WHERE user_id = :u"
        ),
        {"s": json.dumps(section), "u": user_id},
    )
