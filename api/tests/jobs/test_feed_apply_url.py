"""A feed that names the employer's application address apart from the posting's page (owner,
2026-11-01: "When I'm here, how can I continue applying ??"): the address is stored as
`jobs.apply_url`, the posting link the person sees stays the board's page, and an application
opens the employer's form. Invented payloads only, no real job site."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.feeds import himalayas
from app.jobs.models import Job
from tests.jobs.sweep_helpers import new_sweep, search, set_profile
from tests.jobs.test_job_feeds import Api as FakeApi
from tests.jobs.test_job_feeds import fixture
from tests.jobs.test_job_feeds_sweep import run_feeds

EMPLOYER = "https://careers.example-cloud.test/apply/genai-engineer"
PAGE = "https://himalayas.app/companies/example-cloud/jobs/genai-engineer"
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def employer_payload() -> dict:
    payload = fixture("himalayas")
    payload["jobs"][0]["applicationLink"] = EMPLOYER
    return payload


def test_an_employer_link_is_the_apply_address_and_the_board_page_the_posting() -> None:
    item = employer_payload()["jobs"][0]
    found = himalayas.finding_of(item, NOW)
    assert found is not None
    assert (found.url, found.apply_url) == (PAGE, EMPLOYER)


def test_a_himalayas_link_keeps_no_apply_address() -> None:
    found = himalayas.finding_of(fixture("himalayas")["jobs"][0], NOW)
    assert found is not None
    assert found.apply_url is None
    assert found.url.startswith(PAGE)


def test_an_unusable_employer_link_is_never_stored() -> None:
    for link in ["http://careers.example-cloud.test/x", "javascript:alert(1)", "https://x", ""]:
        item = {**employer_payload()["jobs"][0], "applicationLink": link}
        assert himalayas.finding_of(item, NOW) is None


def test_the_sweep_stores_the_apply_address_and_keeps_the_posting_link(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(
        factory,
        alice,
        search(
            interactive_sites=[],
            job_feeds=["himalayas"],
            title_include={"terms": ["genai"], "advanced_regex": False},
            location={"terms": ["bordeaux"], "advanced_regex": False},
        ),
    )
    sweep_id = new_sweep(factory, alice)
    run_feeds(factory, sweep_id, FakeApi({("himalayas.app", ""): employer_payload()}), None)
    with factory() as db:
        row = db.execute(
            sa.select(Job.url, Job.apply_url).where(
                Job.user_id == alice, Job.title == "GenAI Engineer"
            )
        ).one()
    assert (row.url, row.apply_url) == (PAGE, EMPLOYER)
