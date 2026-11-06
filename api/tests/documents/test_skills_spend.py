"""POST /api/documents/spend/skills: what saving a draft of the skills section would start (product
audit, chapter 2, owner answer 4 of 2026-11-05: the possible document cost said before saving),
through the app, two e2e people. The draft's fits are those a rescore would store; the postings it
brings to the threshold that have no CV yet are counted with the spend route's own estimate;
`writes` only with the consent, a credential and automatic generation on; nothing is stored."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.documents import consent, estimate, store
from app.documents.models import Author, Kind, Origin
from app.jobs.models import Job
from app.profile import service as profile_service
from tests.documents.conftest import load
from tests.documents.test_auto_api import Who, queue
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url
from tests.jobs.test_rescore_auto import AFTER, BEFORE, TEXTS
from tests.profile.conftest import full

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url", "queue"]

PATH = "/api/documents/spend/skills"


def person(factory: sessionmaker[Session], api: Api, clock: Any, email: str) -> Who:
    who = Who(factory, api, email, clock)
    save(who, "skills", BEFORE.model_dump(mode="json"))
    return who


def save(who: Who, section: str, value: Any) -> None:
    with who.factory() as db:
        profile = profile_service.get_profile(db, who.user, who.clock())
        profile_service.save_section(db, who.user, section, value, profile.revision, who.clock())
        db.commit()


def add(who: Who, text: str, fit: int, *, days_ago: int = 0, **values: Any) -> uuid.UUID:
    with who.factory() as db:
        job = Job(
            user_id=who.user,
            slug=f"example-{uuid.uuid4().hex[:8]}",
            company="Example Robotics",
            title="Engineer",
            posting_text=text,
            fit=fit,
            found_on=who.clock().date() - timedelta(days=days_ago),
            **values,
        )
        db.add(job)
        db.commit()
        return job.id


def written_cv(who: Who, job: uuid.UUID) -> None:
    with who.factory() as db:
        document = store.get_or_create(db, who.user, job, Kind.cv, at=who.clock())
        store.save_draft(
            db,
            who.user,
            document.id,
            load("written/cv_typical.json"),
            document.draft_revision,
            Author.user,
            at=who.clock(),
        )
        store.snapshot(db, who.user, document.id, Origin.edited, at=who.clock())
        db.commit()


def ask(api: Api, who: Who, skills: Any = AFTER) -> dict[str, Any]:
    body = {"skills": skills.model_dump(mode="json") if hasattr(skills, "model_dump") else skills}
    response = api.post(PATH, token=who.token, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def fits(who: Who) -> list[int | None]:
    with who.factory() as db:
        return list(
            db.scalars(sa.select(Job.fit).where(Job.user_id == who.user).order_by(Job.slug))
        )


def test_the_draft_counts_the_postings_it_brings_to_the_threshold_with_their_cost(
    factory: sessionmaker[Session], api: Api, clock: Any, app: FastAPI
) -> None:
    alex = person(factory, api, clock, "e2e-skills-spend-alex@example.org")
    alex.credential()
    for text, before, _ in TEXTS:
        add(alex, text, before)
    written = add(alex, TEXTS[1][0], 0)  # its CV is written already: nothing more to spend
    written_cv(alex, written)
    add(alex, TEXTS[1][0], 0, days_ago=40)  # out of the 30 days: never written automatically
    add(alex, TEXTS[1][0], 10, fit_overridden=True)  # a fit set by hand: a rescore keeps it
    add(alex, TEXTS[1][0], 0, status="applied")  # past the open statuses
    blake = person(factory, api, clock, "e2e-skills-spend-blake@example.org")
    add(blake, TEXTS[1][0], 0)
    before = fits(alex)

    answer = ask(api, alex)
    assert (answer["auto"], answer["min_fit"], answer["writes"], answer["newly"]) == (
        True,
        70,
        True,
        2,
    )
    with factory() as db:
        one = consent.figures(db, alex.user, clock().date()).posting
    both = estimate.times(one, 2)
    assert answer["newly_estimate"]["tokens"] == {"low": both.low, "high": both.high}
    assert answer["newly_estimate"]["cost"] is not None  # an API key: a cost
    assert answer["priced"] is True
    assert fits(alex) == before  # nothing stored
    # The draft Blake would save counts Blake's postings only.
    assert ask(api, blake)["newly"] == 1


def test_no_write_without_the_credential_and_nothing_when_automatic_writing_is_off(
    factory: sessionmaker[Session], api: Api, clock: Any, app: FastAPI
) -> None:
    alex = person(factory, api, clock, "e2e-skills-spend-off@example.org")
    add(alex, TEXTS[0][0], 50)
    without = ask(api, alex)
    assert (without["writes"], without["newly"]) == (False, 1)  # waits for a credential
    assert without["cost_note"]
    save(alex, "search", {**full("search"), "documents": {"auto": False}})
    off = ask(api, alex)
    assert (off["auto"], off["writes"], off["newly"]) == (False, False, 0)


def test_the_persons_threshold_and_an_invalid_draft(
    factory: sessionmaker[Session], api: Api, clock: Any, app: FastAPI
) -> None:
    alex = person(factory, api, clock, "e2e-skills-spend-limit@example.org")
    alex.credential()
    add(alex, TEXTS[0][0], 50)  # 88 with the draft
    save(alex, "search", {**full("search"), "documents": {"min_fit": 90}})
    assert ask(api, alex)["newly"] == 0
    invalid = api.post(PATH, token=alex.token, json={"skills": {"have": "not a list"}})
    assert invalid.status_code == 422
    extra = api.post(PATH, token=alex.token, json={"skills": {}, "other": 1})
    assert extra.status_code == 422
    assert api.post(PATH, json={"skills": {}}).status_code in (401, 403)
