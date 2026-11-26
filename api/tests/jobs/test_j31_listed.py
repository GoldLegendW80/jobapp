"""Jobs J31 (owner, 2026-11-26: "Oui, avec une mention"): a posting kept from a card of a list is
stored with the list's address (`jobs.list_url`, migration 0052) and its card's text, counted per
source; a full report of its address by a later search replaces the card's text, scores it again
and removes the mention; the Jobs list filters it, Discover never takes it, and Write now and
Apply say why they wait. Invented postings only."""

import dataclasses
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import guardrails
from app.jobs import service
from app.jobs.models import Job, JobEvent, JobEventKind
from tests.jobs.sweep_helpers import finding, jobs_of, new_sweep, run, search, set_profile

LIST = "https://aggregator.example.test/emplois?q=data"


def card(title: str = "GenAI engineer", **fields) -> object:
    full = finding(title, **fields)
    return dataclasses.replace(
        full,
        text=f"{title} Acme Robotics Paris",
        evidence={"title": title, "rule": "card", "card": "page", "list": LIST},
        list_url=LIST,
    )


def test_a_card_is_stored_with_its_list_then_its_page_read_replaces_it(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    listed = card()
    first = run(factory, new_sweep(factory, alice), listed)
    [job] = jobs_of(factory, alice)
    assert (job.list_url, job.posting_text) == (LIST, "GenAI engineer Acme Robotics Paris")
    assert (first.stats["new"], first.stats["listed"]) == (1, 1)
    assert first.stats["sources"][0]["listed"] == 1
    again = run(factory, new_sweep(factory, alice), listed)  # the same card: a duplicate
    assert (again.stats["duplicates"], again.stats["listed"], again.stats["listed_read"]) == (
        1,
        0,
        0,
    )
    assert jobs_of(factory, alice)[0].list_url == LIST

    page = dataclasses.replace(finding("GenAI engineer"), url=listed.url)
    read = run(factory, new_sweep(factory, alice), page)
    [same] = jobs_of(factory, alice)
    assert same.id == job.id
    assert same.list_url is None
    assert same.posting_text == page.text
    assert same.read_at is None
    assert (read.stats["duplicates"], read.stats["listed_read"]) == (1, 1)
    assert read.stats["sources"][0]["listed_read"] == 1
    with factory() as db:
        events = db.scalars(
            sa.select(JobEvent.data).where(
                JobEvent.job_id == job.id, JobEvent.kind == JobEventKind.sweep
            )
        ).all()
    assert {"page_read": {"list_url": LIST}}.items() <= events[-1].items()
    # A card found after the page was read changes nothing.
    run(factory, new_sweep(factory, alice), listed)
    assert jobs_of(factory, alice)[0].posting_text == page.text


def test_the_list_filters_it_and_write_now_and_apply_say_why_they_wait(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card(), finding("Applied AI engineer"))
    with factory() as db:

        def titles(choice: service.ListingChoice) -> list[str]:
            filters = service.JobFilters(listing=choice)
            found = db.scalars(service.filtered(alice, filters)).all()
            return sorted(job.title for job in found)

        assert titles(service.ListingChoice.all) == ["Applied AI engineer", "GenAI engineer"]
        assert titles(service.ListingChoice.only) == ["GenAI engineer"]
        assert titles(service.ListingChoice.without) == ["Applied AI engineer"]
        listed = db.scalar(sa.select(Job).where(Job.user_id == alice, Job.list_url.is_not(None)))
        full = db.scalar(sa.select(Job).where(Job.user_id == alice, Job.list_url.is_(None)))
        assert service.listed_refusal(db, alice, listed.id) == service.LISTED_WAITS
        assert service.listed_refusal(db, alice, full.id) is None
        assert service.listed_refusal(db, bob, listed.id) is None  # another person's: nothing
        assert "its text is needed first" in guardrails.SENTENCES["posting_listed"]
