"""The scaffolding of the opportunities tests (O1). Later tasks extend it only by adding files:
their tests in `test_o<n>_*.py` and helpers in `o<n>_support.py`; they never edit this one.

Everything here is invented: person A (e2e-deck-a@example.org) and person B
(e2e-deck-b@example.org), both signed in, both with a profile whose search section holds one
ticked job website, "Show postings from" 70 and the owner's never apply rule (apply A2's), and
the skills of the jobs tests. No real person, company or site.

Fixtures a test gets by naming them:

- `person_a`, `person_b`: `Person` (session token, user id, e-mail);
- `swept`: postings found by a sweep whose fake website finds the given findings;
- the identity fixtures: `api` (the app with a frozen clock, cookie explicit client), `clock`,
  `factory`, `db`, `engine`, `migrated_url` (a real Postgres at head).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import Application, Mode, State
from app.browser.finder import VerifiedFinding
from app.identity.models import User
from app.jobs.models import Job, JobEvent, JobStatus
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user
from tests.jobs.sweep_helpers import new_sweep, run, search, set_profile

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock
app = identity.app
api = identity.api

__all__ = ["api", "app", "clock", "db", "engine", "factory", "migrated_url"]

USER_A = "e2e-deck-a@example.org"
USER_B = "e2e-deck-b@example.org"
OWNER_RULE = "No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"
FOUND_ON = date(2026, 10, 7)
AT = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)


@dataclass(frozen=True)
class Person:
    token: str
    user_id: uuid.UUID
    email: str


def deck_search(**overrides: Any) -> dict[str, Any]:
    """The search section of A and B: one ticked website, min_fit 70, the owner's rule."""
    values: dict[str, Any] = {"min_fit": 70, "never_apply": [OWNER_RULE]}
    values.update(overrides)
    return search(**values)


def _person(factory: sessionmaker[Session], api: Api, email: str) -> Person:
    make_user(factory, email=email, name=email.split("@")[0])
    token = api.login_token(email)
    with factory() as session:
        user_id = session.execute(sa.select(User.id).where(User.email == email)).scalar_one()
    set_profile(factory, user_id, deck_search())
    return Person(token, user_id, email)


@pytest.fixture
def person_a(factory: sessionmaker[Session], api: Api) -> Person:
    return _person(factory, api, USER_A)


@pytest.fixture
def person_b(factory: sessionmaker[Session], api: Api) -> Person:
    return _person(factory, api, USER_B)


def add_posting(factory: sessionmaker[Session], owner: Person, **fields: Any) -> uuid.UUID:
    """One posting of `owner`, straight in the database: found, fit 80, text, Example Robotics
    unless the fields say otherwise."""
    unique = uuid.uuid4().hex[:10]
    values: dict[str, Any] = {
        "slug": f"posting-{unique}",
        "company": "Example Robotics",
        "title": f"Applied AI Engineer {unique}",
        "location": "Paris",
        "url": f"https://jobs.example.org/{unique}",
        "source": "ashby:fake-ok",
        "fit": 80,
        "posting_text": "Example Robotics builds warehouse robots.",
        "found_on": FOUND_ON,
        "created_at": AT,
        "updated_at": AT,
    }
    values.update(fields)
    with factory() as session, session.begin():
        posting = Job(user_id=owner.user_id, **values)
        session.add(posting)
        session.flush()
        return posting.id


def add_application(
    factory: sessionmaker[Session], owner: Person, posting_id: uuid.UUID, state: State
) -> uuid.UUID:
    """An application of `owner` on the posting, in `state` (a Like, as apply stores it)."""
    clicked = state in (State.submitted, State.submit_unknown)
    with factory() as session, session.begin():
        application = Application(
            id=uuid.uuid4(),
            user_id=owner.user_id,
            posting_id=posting_id,
            mode=Mode.review,
            dry_run=False,
            state=state,
            ats="ashby",
            form_url=f"https://jobs.ashbyhq.com/example/{uuid.uuid4()}",
            canonical_key=f"ashby:example:{uuid.uuid4()}",
            created_at=AT,
            updated_at=AT,
            submitted_at=AT if clicked else None,
        )
        session.add(application)
        return application.id


def get_deck(api: Api, who: Person, **params: Any) -> httpx.Response:
    return api.get("/api/postings/deck", token=who.token, params=params)


def deck_ids(api: Api, who: Person, **params: Any) -> list[str]:
    response = get_deck(api, who, **params)
    assert response.status_code == 200, response.text
    return [card["id"] for card in response.json()["cards"]]


def walk(api: Api, who: Person, **params: Any) -> list[list[str]]:
    """Every page of the deck, following the cursor to its end."""
    pages: list[list[str]] = []
    cursor = None
    while True:
        response = get_deck(api, who, **params, **({"cursor": cursor} if cursor else {}))
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append([card["id"] for card in body["cards"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 100, "the cursor never ends"


def pass_(api: Api, who: Person, posting_id: uuid.UUID | str) -> httpx.Response:
    return api.post(f"/api/postings/{posting_id}/pass", token=who.token)


def undo(api: Api, who: Person, posting_id: uuid.UUID | str) -> httpx.Response:
    return api.request("DELETE", f"/api/postings/{posting_id}/pass", token=who.token)


def status_of(factory: sessionmaker[Session], posting_id: uuid.UUID) -> JobStatus:
    with factory() as session:
        return session.execute(sa.select(Job.status).where(Job.id == posting_id)).scalar_one()


def status_events(factory: sessionmaker[Session], posting_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.execute(
            sa.select(JobEvent.data)
            .where(JobEvent.job_id == posting_id, JobEvent.kind == "status")
            .order_by(JobEvent.id)
        ).scalars()
        return list(rows)


def swept(factory: sessionmaker[Session], owner: Person, *findings: VerifiedFinding) -> list[Job]:
    """Run one sweep whose website finds `findings`; the owner's postings after it."""
    run(factory, new_sweep(factory, owner.user_id), *findings)
    with factory() as session:
        return list(
            session.execute(
                sa.select(Job).where(Job.user_id == owner.user_id).order_by(Job.title)
            ).scalars()
        )
