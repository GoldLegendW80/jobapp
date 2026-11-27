"""Jobs J31b (issue #830, item 5): the person sets the text of a posting seen only in a list.
PUT /api/jobs/{id}/text stores the pasted text as the posting's, scores it again, has the
posting reader read it again and removes the mention, with an `edit` event naming the list, as a
later search's read of its page does (`service.page_text`, shared with `sweep._page_read`); the
never apply rules and the unwanted titles and places of the posting's search apply (the
orchestrator's default of the J31 decision: a status the person set stays). A text too short to
be a posting's, a posting no longer seen in a list, another person's posting and an unreadable
search change nothing. Invented postings only."""

import dataclasses
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import schemas, service, sweep
from app.jobs.models import Job, JobEvent, JobStatus, SavedSearch
from app.profile.models import Profile
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.sweep_helpers import (
    SKILLS,
    finding,
    jobs_of,
    new_sweep,
    run,
    search,
    set_profile,
)
from tests.jobs.test_api import Signed, seed, sign_in

LIST = "https://aggregator.example.test/emplois?q=data"
CARD = "GenAI engineer Acme Robotics Paris"
PASTED = (
    "GenAI engineer at Acme Robotics, Paris.\n\n"
    "About the role: you build agents with tool use in Python, LLM evaluation pipelines and "
    "retrieval (RAG) for our warehouse robots. You ship to production every week with a small "
    "team, write tests, and talk to the operators who use what you build.\n\n"
    "How to apply: send your CV through the form on this page; we answer within two weeks."
)


@pytest.fixture
def person(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-paste@example.org")


@pytest.fixture
def other(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-other@example.org")


def put_text(api: Api, who: Signed, job_id: Any, text: str) -> httpx.Response:
    return api.request("PUT", f"/api/jobs/{job_id}/text", token=who.token, json={"text": text})


def listed(factory: sessionmaker[Session], who: Signed, **fields: Any) -> Job:
    row = {"title": "GenAI engineer", "company": "Acme Robotics", "location": "Paris"}
    row.update(list_url=LIST, posting_text=CARD, description=CARD, **fields)
    [job] = seed(factory, who.id, [row])
    return job


def stored(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        _ = job.posting_text
        db.expunge(job)
        return job


def events_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[tuple[str, dict]]:
    with factory() as db:
        rows = db.scalars(
            sa.select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
        return [(event.kind.value, event.data) for event in rows]


def test_the_pasted_text_replaces_the_cards_scores_it_and_removes_the_mention(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    set_profile(factory, person.id, search())
    job = listed(factory, person, fit=None)
    response = put_text(api, person, job.id, f"  {PASTED}\n")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["list_url"], body["posting_text"]) == (None, PASTED)
    after = stored(factory, job.id)
    assert (after.list_url, after.posting_text, after.status) == (None, PASTED, JobStatus.found)
    assert after.description == PASTED[: service.DESCRIPTION_CHARS]
    assert after.read_at is None
    assert after.read_attempts == 0
    assert after.fit is not None
    assert after.competencies
    assert after.updated_at > NOW
    kinds = events_of(factory, job.id)
    assert ("edit", {"text_pasted": {"list_url": LIST}}) in kinds
    assert ("fit", {"from": None, "to": after.fit, "manual": False, "via": "pasted"}) in kinds
    # Its page read since: the mention is gone, a second paste is refused.
    again = put_text(api, person, job.id, PASTED)
    assert (again.status_code, again.json()["code"]) == (409, "page_read")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\t ",
        CARD,
        "x" * (service.TEXT_MIN_CHARS - 1),
        # Spaces and line breaks do not make a text long enough.
        "GenAI engineer\n" + " " * 600 + "\n" * 300 + "Paris",
    ],
)
def test_a_text_too_short_to_be_a_posting_is_refused_with_a_sentence(
    factory: sessionmaker[Session], api: Api, person: Signed, text: str
) -> None:
    set_profile(factory, person.id, search())
    job = listed(factory, person)
    response = put_text(api, person, job.id, text)
    assert response.status_code == 422
    assert response.json() == {"detail": service.TEXT_SHORT, "code": "text_too_short"}
    after = stored(factory, job.id)
    assert (after.list_url, after.posting_text) == (LIST, CARD)


def test_a_text_at_the_minimum_is_taken_and_one_over_the_maximum_refused(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    assert schemas.PASTED_MAX == sweep.POSTING_TEXT_MAX
    set_profile(factory, person.id, search())
    job = listed(factory, person)
    long = put_text(api, person, job.id, "a" * (schemas.PASTED_MAX + 1))
    assert long.status_code == 422
    assert stored(factory, job.id).list_url == LIST
    least = "b" * service.TEXT_MIN_CHARS
    assert put_text(api, person, job.id, least).status_code == 200
    assert stored(factory, job.id).posting_text == least


def test_an_ordinary_posting_another_persons_and_an_unknown_one_change_nothing(
    factory: sessionmaker[Session], api: Api, person: Signed, other: Signed
) -> None:
    set_profile(factory, person.id, search())
    [ordinary] = seed(factory, person.id, [{"title": "Applied AI engineer", "posting_text": "x"}])
    refused = put_text(api, person, ordinary.id, PASTED)
    assert refused.status_code == 409
    assert refused.json() == {"detail": service.PAGE_READ_ALREADY, "code": "page_read"}
    assert stored(factory, ordinary.id).posting_text == "x"
    theirs = listed(factory, other)
    assert put_text(api, person, theirs.id, PASTED).status_code == 404
    assert stored(factory, theirs.id).list_url == LIST
    assert put_text(api, person, uuid.uuid4(), PASTED).status_code == 404
    assert put_text(api, person, "not-an-id", PASTED).status_code == 404


def test_a_never_apply_rule_on_its_title_skips_it_with_the_rule_as_its_note(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    set_profile(factory, person.id, search())
    job = listed(factory, person, title="Security GenAI engineer")
    assert put_text(api, person, job.id, PASTED).status_code == 200
    after = stored(factory, job.id)
    rule = search()["never_apply"][0]
    assert (after.status, after.notes, after.list_url) == (JobStatus.skipped, rule, None)
    kinds = events_of(factory, job.id)
    assert ("status", {"from": "found", "to": "skipped", "via": "never_apply"}) in kinds
    assert ("note", {"from": "", "to": rule, "via": "never_apply"}) in kinds


EXCLUDED = {
    "title_exclude": {"terms": ["stage"], "advanced_regex": False},
    "location_exclude": {"terms": ["lyon"], "advanced_regex": False},
}


@pytest.mark.parametrize(
    ("title", "location", "note"),
    [
        ("GenAI engineer (Stage)", "Paris", "Unwanted title: Stage"),
        ("GenAI engineer", "Lyon, France", "Unwanted place: Lyon"),
    ],
)
def test_an_unwanted_title_or_place_skips_it_while_it_is_found(
    factory: sessionmaker[Session],
    api: Api,
    person: Signed,
    title: str,
    location: str,
    note: str,
) -> None:
    set_profile(factory, person.id, search(**EXCLUDED))
    job = listed(factory, person, title=title, location=location)
    assert put_text(api, person, job.id, PASTED).status_code == 200
    after = stored(factory, job.id)
    assert (after.status, after.notes, after.list_url) == (JobStatus.skipped, note, None)
    assert ("status", {"from": "found", "to": "skipped", "via": "unwanted"}) in events_of(
        factory, job.id
    )


def test_a_status_the_person_set_and_a_fit_set_by_hand_stay(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    set_profile(factory, person.id, search(**EXCLUDED))
    job = listed(
        factory,
        person,
        title="GenAI engineer (Stage)",
        status=JobStatus.shortlisted,
        notes="Ask Lea",
        fit=91,
        fit_overridden=True,
    )
    assert put_text(api, person, job.id, PASTED).status_code == 200
    after = stored(factory, job.id)
    assert (after.status, after.notes, after.fit, after.list_url) == (
        JobStatus.shortlisted,
        "Ask Lea",
        91,
        None,
    )
    assert [kind for kind, _ in events_of(factory, job.id)] == ["edit"]


def test_the_exclusions_read_are_those_of_the_search_that_found_it(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    set_profile(factory, person.id, search())
    criteria = {key: value for key, value in search(**EXCLUDED).items() if key != "never_apply"}
    with factory() as db, db.begin():
        saved = SavedSearch(
            user_id=person.id, name="Lyon", criteria=criteria, schedule={}, revision=1
        )
        db.add(saved)
        db.flush()
        saved_id = saved.id
    theirs = listed(factory, person, location="Lyon", criteria_search_id=saved_id)
    mains = listed(factory, person, location="Lyon", slug="main-one", url="https://e.test/2")
    assert put_text(api, person, theirs.id, PASTED).status_code == 200
    assert put_text(api, person, mains.id, PASTED).status_code == 200
    assert stored(factory, theirs.id).status is JobStatus.skipped
    assert stored(factory, mains.id).status is JobStatus.found


def test_a_search_that_no_longer_reads_refuses_the_text_and_keeps_the_card(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    with factory() as db, db.begin():
        sections = {"search": {"days": "every day"}, "skills": SKILLS}
        db.add(Profile(user_id=person.id, sections=sections, revision=1, updated_at=NOW))
    job = listed(factory, person)
    response = put_text(api, person, job.id, PASTED)
    assert response.status_code == 409
    assert response.json()["code"] == "search_unreadable"
    assert stored(factory, job.id).list_url == LIST


def test_a_card_a_search_kept_takes_the_pasted_text_and_the_next_read_of_its_page_is_a_duplicate(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    set_profile(factory, person.id, search())
    page = finding("GenAI engineer")
    card = dataclasses.replace(
        page,
        text=CARD,
        evidence={"title": "GenAI engineer", "rule": "card", "card": "page", "list": LIST},
        list_url=LIST,
    )
    run(factory, new_sweep(factory, person.id), card)
    [job] = jobs_of(factory, person.id)
    assert put_text(api, person, job.id, PASTED).status_code == 200
    read = run(factory, new_sweep(factory, person.id), page)
    assert (read.stats["duplicates"], read.stats["listed_read"]) == (1, 0)
    assert jobs_of(factory, person.id)[0].posting_text == PASTED
