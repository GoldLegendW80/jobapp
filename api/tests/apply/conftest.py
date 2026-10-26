"""The scaffolding of the apply module's tests (A1). Later tasks extend it only by adding files:
helpers in `a<n>_support.py` and pages under `tests/ats_forms/a<n>/`; they never edit this one.

Everything here is invented: "Alex Example" (person A, e2e-apply-a@example.org) and "Blake
Sample" (person B, e2e-apply-b@example.org) both have a profile, and both hold a posting at the
same invented company on the fake ATS ("Example Robotics"), each with a CV and a letter whose
PDF is a fixed stand-in for what the renderer returns. No real person, company or site.

Fixtures a test gets by naming them:

- `forms`: the fake ATS forms server (tests/ats_forms_stub.py) in process on a loopback port,
  reset for the test; `forms.base` is its address, `forms.submissions()` what it recorded. With
  `APPLY_STUB_BASE_URL` set, `forms_base_url` names the stub of a running test stack instead;
- `person_a`, `person_b`: `Person` (user id, e-mail, the posting at Example Robotics on the fake
  Ashby, its CV and letter version ids). The database fixtures are those of the documents tests
  (`db`, `factory`, `engine`, `migrated_url`): a real Postgres at head.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.documents import store
from app.documents.models import Author, FitResult, Kind, Origin
from app.profile import service as profile_service
from tests.apply import a1_support
from tests.documents.conftest import load
from tests.documents.store_support import (
    db,
    engine,
    factory,
    migrated_url,
)
from tests.profile.conftest import full

__all__ = ["db", "engine", "factory", "migrated_url"]

AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
USER_A = "e2e-apply-a@example.org"
USER_B = "e2e-apply-b@example.org"


@dataclass
class Person:
    user_id: uuid.UUID
    email: str
    posting_id: uuid.UUID
    cv_version_id: uuid.UUID
    letter_version_id: uuid.UUID


@pytest.fixture
def forms() -> Iterator[a1_support.Forms]:
    with a1_support.running() as server:
        yield server


@pytest.fixture
def forms_base_url(forms: a1_support.Forms) -> str:
    return os.environ.get("APPLY_STUB_BASE_URL") or forms.base


def _person(db: Session, email: str, name: str, url: str) -> Person:
    user = db.execute(
        sa.text("INSERT INTO users (email, name, password_hash) VALUES (:e, :n, 'h') RETURNING id"),
        {"e": email, "n": name},
    ).scalar_one()
    profile = profile_service.get_profile(db, user, AT)
    for section in ("identity", "experience", "skills", "answers"):
        content = full(section)
        if section == "answers":
            # The synthetic agent scripts need actual personal sources for their
            # stock intent and achievement claims. Why questions still call the
            # model; tests assert those calls and separately refuse unsourced intent.
            content = {
                **content,
                "motivation": content["motivation"]
                + "\nI want to build agents for warehouse operations."
                + "\nI want to build agents. I build agents.",
            }
        profile = profile_service.save_section(db, user, section, content, profile.revision, AT)
    posting, versions = add_posting(db, user, url)
    db.commit()
    return Person(user, email, posting, versions[Kind.cv], versions[Kind.letter])


def add_posting(db: Session, user: uuid.UUID, url: str) -> tuple[uuid.UUID, dict[Kind, uuid.UUID]]:
    """A posting of Example Robotics' Applied AI Engineer at `url`, with a CV and a letter whose
    versions have a one page PDF. The caller commits."""
    posting = db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url, posting_text) "
            "VALUES (:u, :s, 'Example Robotics', 'Applied AI Engineer', :url, :t) RETURNING id"
        ),
        {
            "u": user,
            "s": f"example-robotics-{uuid.uuid4().hex[:8]}",
            "url": url,
            "t": "Example Robotics builds warehouse robots. You will build agents for operations.",
        },
    ).scalar_one()
    versions = {}
    for kind, content in (
        (Kind.cv, load("written/cv_typical.json")),
        (Kind.letter, load("written/letter_typical.json")),
    ):
        document = store.get_or_create(db, user, posting, kind, at=AT)
        store.save_draft(
            db, user, document.id, content, document.draft_revision, Author.user, at=AT
        )
        made, _ = store.snapshot(db, user, document.id, Origin.edited, at=AT)
        store.attach_pdf(
            db,
            user,
            document.id,
            made.number,
            a1_support.PDF,
            fit=FitResult.one_page,
            fit_step=0,
            pages=1,
        )
        versions[kind] = made.id
    return posting, versions


@pytest.fixture
def person_a(db: Session, forms_base_url: str) -> Person:
    return _person(
        db,
        USER_A,
        "Alex Example",
        forms_base_url + "/ashby/example-robotics/applied-ai-engineer/application",
    )


@pytest.fixture
def person_b(db: Session, forms_base_url: str) -> Person:
    return _person(
        db,
        USER_B,
        "Blake Sample",
        forms_base_url + "/ashby/example-robotics/applied-ai-engineer/application",
    )
