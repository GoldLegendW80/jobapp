"""Jobs J36 (owner, 2026-11-27: "Oui", LinkedIn signed in included): after a website's agent,
the app reads the pages of the postings kept from a card (J31) through the real website Runner
and the run's own commits: a page that shows the posting replaces the card's text as a search's
read does, a gone page closes it as the liveness check does, a failed reading is written once a
day and after three days the posting is no longer tried and says so; the counters the summary
serves stay true. Invented postings only."""

import asyncio
import dataclasses
import uuid
from datetime import timedelta
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import finder
from app.jobs import router as jobs_router
from app.jobs import service, sweep, sweep_router
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from tests.browser.fakes import FakeLeases, ScriptedFinder
from tests.browser.test_j36_listed_pages import ELSEWHERE, Board, posting
from tests.jobs.sweep_helpers import (
    HOST,
    SITE,
    finding,
    jobs_of,
    new_sweep,
    now,
    read_sweep,
    run,
    search,
    set_profile,
    website_runner,
)

LIST = "https://jobs.example-robotics.test/search?q=genai"
START = SITE["url"]


def card(title: str, **fields) -> finder.VerifiedFinding:
    full = finding(title, **fields)
    return dataclasses.replace(
        full,
        text=f"{title} Acme Robotics Paris",
        evidence={"title": title, "rule": "card", "card": "page", "list": LIST},
        list_url=LIST,
    )


def url_of(title: str) -> str:
    return finding(title).url


async def nothing(session: finder.FinderSession) -> finder.Ran:
    return finder.Ran()


def search_with_pages(
    factory: sessionmaker[Session], user: uuid.UUID, board: Board, **options
) -> sweep.Sweep:
    """A search whose website's agent reports nothing, then reads the pages (J36)."""
    runner = website_runner(board, FakeLeases(), finder=ScriptedFinder(nothing), **options)
    return run(factory, new_sweep(factory, user), browser=runner.websites)


def test_the_pages_of_the_postings_kept_from_a_card_are_read_after_the_agent(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    titles = ("GenAI engineer", "AI engineer", "Forward deployed engineer")
    run(factory, new_sweep(factory, alice), *(card(t) for t in titles))
    assert {job.list_url for job in jobs_of(factory, alice)} == {LIST}
    board = Board(
        {
            url_of(titles[0]): posting("GenAI engineer"),
            url_of(titles[1]): "",
            url_of(titles[2]): ELSEWHERE,
        },
        start=START,
        statuses={url_of(titles[1]): 404},
    )
    row = search_with_pages(factory, alice, board)
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    read = jobs["GenAI engineer"]
    assert read.list_url is None
    assert "invented robot arms" in read.posting_text
    assert jobs["AI engineer"].status is JobStatus.closed
    assert jobs["AI engineer"].liveness["verdict"] == "closed"
    assert jobs["AI engineer"].list_url == LIST  # closed, its text still the card's
    unread = jobs["Forward deployed engineer"]
    assert (unread.list_url, unread.status) == (LIST, JobStatus.found)
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert entry["listed_pages"] == {
        "read": 1, "closed": 1, "unread": 1, "given_up": 0, "ended": "done", "left": 1,
    }  # fmt: skip
    # The counters stay true: one card read, nothing new, no duplicate, nothing found.
    assert (entry["listed_read"], entry["found"], entry["postings"], entry["duplicates"]) == (
        1, 0, 0, 0,
    )  # fmt: skip
    assert (row.stats["listed_read"], row.stats["new"], row.stats["duplicates"]) == (1, 0, 0)
    [source] = [s for s in sweep_router.sweep_out(row).sources if s.host == HOST]
    assert source.listed_pages is not None
    assert (source.listed_pages.read, source.listed_pages.left, source.listed_pages.ended) == (
        1, 1, "done",
    )  # fmt: skip
    with factory() as db:
        kinds = db.scalars(
            sa.select(JobEvent.data).where(
                JobEvent.job_id == unread.id, JobEvent.kind == JobEventKind.sweep
            )
        ).all()
    unread_events = [k for k in kinds if service.PAGE_UNREAD in k]
    assert len(unread_events) == 1
    assert unread_events[0][service.PAGE_UNREAD]["tries"] == 1
    # The same day again: the page is tried, no second failed reading is written.
    search_with_pages(factory, alice, board)
    with factory() as db:
        count = db.scalar(
            sa.select(sa.func.count())
            .select_from(JobEvent)
            .where(JobEvent.job_id == unread.id, JobEvent.data.has_key(service.PAGE_UNREAD))
        )
    assert count == 1


def test_after_three_days_of_failed_readings_it_is_no_longer_tried_and_says_so(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"))
    [job] = jobs_of(factory, alice)
    with factory() as db, db.begin():
        for days in (2, 1):
            db.add(
                JobEvent(
                    job_id=job.id, user_id=alice, kind=JobEventKind.sweep,
                    data={service.PAGE_UNREAD: {"list_url": LIST}}, at=now() - timedelta(days=days),
                )
            )  # fmt: skip
    board = Board({url_of("GenAI engineer"): ELSEWHERE}, start=START)
    row = search_with_pages(factory, alice, board)
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert (entry["listed_pages"]["given_up"], entry["listed_pages"]["left"]) == (1, 0)
    with factory() as db:
        assert service.pages_given_up(db, alice, [job.id]) == {job.id}
        user = SimpleNamespace(id=alice)
        stored = db.get(Job, job.id)
        item = jobs_router._item(stored, 0, jobs_router._given_up(db, user, [stored]))
        assert item.page_given_up is True
        assert item.list_url == LIST
    opened = len(board.opened())
    again = search_with_pages(factory, alice, board)  # no longer tried
    assert len(board.opened()) == opened
    [entry] = [s for s in again.stats["sources"] if s.get("host") == HOST]
    assert entry["listed_pages"]["read"] + entry["listed_pages"]["unread"] == 0


def test_the_persons_exclusions_and_never_apply_rules_apply_as_in_a_search(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(title_exclude={"terms": ["stage"], "advanced_regex": False}))
    run(factory, new_sweep(factory, alice), card("GenAI engineer"), card("AI engineer"))
    board = Board(
        {
            url_of("GenAI engineer"): posting("GenAI engineer (Stage)"),
            url_of("AI engineer"): posting("AI engineer, cyber security"),
        },
        start=START,
    )
    row = search_with_pages(factory, alice, board)
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    staged = jobs["GenAI engineer"]
    assert (staged.status, staged.notes, staged.list_url) == (
        JobStatus.skipped, "Unwanted title: Stage", None,
    )  # fmt: skip
    ruled = jobs["AI engineer"]
    assert (ruled.status, ruled.list_url) == (JobStatus.skipped, None)  # the never apply rule
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert entry["skipped_by_rule"] == 1
    assert entry["listed_read"] == 2


def test_another_persons_and_another_websites_postings_are_never_opened(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    set_profile(factory, bob, search())
    run(factory, new_sweep(factory, bob), card("GenAI engineer"))
    other = dataclasses.replace(card("AI engineer"), url="https://careers.example-labs.test/j/1")
    run(factory, new_sweep(factory, alice), other)
    with factory() as db, db.begin():  # found by another website: its source names that one
        db.execute(
            sa.update(Job).where(Job.user_id == alice).values(source="careers.example-labs.test")
        )
    board = Board({}, start=START)
    search_with_pages(factory, alice, board)
    assert board.opened() == [START]  # the agent's start page alone


def test_the_store_after_the_sweep_ended_writes_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"))
    [job] = jobs_of(factory, alice)
    sweep_id = new_sweep(factory, alice)
    with factory() as db, db.begin():
        db.execute(sa.update(sweep.Sweep).where(sweep.Sweep.id == sweep_id).values(status="done"))
    plan = SimpleNamespace(sweep_id=sweep_id, user_id=alice, known=None)
    listed = sweep.ListedPage(job.id, job.url, "unread")
    stored = sweep.commit_verified(
        factory, plan, sweep.Known.__new__(sweep.Known), sweep.Stats(), "s", HOST, (), now(),
        listed=listed,
    )  # fmt: skip
    assert stored is None
    assert asyncio.run(asyncio.sleep(0)) is None
    with factory() as db:
        assert not db.scalar(
            sa.select(sa.exists().where(JobEvent.data.has_key(service.PAGE_UNREAD)))
        )
    assert read_sweep(factory, sweep_id).status.value == "done"
