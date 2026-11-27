"""Jobs J36 (owner, 2026-11-27: "Oui", LinkedIn signed in included): after a website's agent,
the app reads the pages of the postings kept from a card (J31) through the real website Runner
and the run's own commits: a page that shows the posting replaces the card's text as a search's
read does, a gone page closes it as the liveness check does, a failed reading is written once a
day and after three days the posting is no longer tried and says so; the counters the summary
serves stay true. Invented postings only."""

import dataclasses
import uuid
from datetime import timedelta
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import finder
from app.jobs import liveness, service, sweep, sweep_router
from app.jobs import router as jobs_router
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
    assert entry["result_pages"] == 1  # the agent's start page: never the pages read after it
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
    assert url_of("GenAI engineer") not in board.opened()[opened:]
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
    # Review of PR 864, item 2: kept from another board's card, then found here too: its source
    # names both websites, its address is the other board's.
    both = dataclasses.replace(card("GenAI engineer"), url="https://www.other-board.test/viewjob")
    run(factory, new_sweep(factory, alice), other, both)
    with factory() as db, db.begin():  # found by another website: its source names that one
        db.execute(
            sa.update(Job)
            .where(Job.user_id == alice, Job.url == other.url)
            .values(source="careers.example-labs.test")
        )
        db.execute(
            sa.update(Job)
            .where(Job.user_id == alice, Job.url == both.url)
            .values(source=f"www.other-board.test, {HOST}")
        )
    board = Board({}, start=START)
    row = search_with_pages(factory, alice, board)
    assert set(board.opened()) == {START}  # the agent's start page alone
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert entry["listed_pages"]["left"] == 0  # neither waits for this website's reading


def test_the_store_after_the_sweep_ended_writes_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"))
    [job] = jobs_of(factory, alice)
    sweep_id = new_sweep(factory, alice)
    plan = sweep.claim(factory, sweep_id, now())
    assert plan is not None
    with factory() as db, db.begin():
        db.execute(
            sa.update(sweep.Sweep)
            .where(sweep.Sweep.id == sweep_id)
            .values(status="done", finished_at=now())
        )
    listed = sweep.ListedPage(job.id, job.url, "unread")
    stored = sweep.commit_verified(
        factory, plan, plan.known.copy(), sweep.Stats(), "s", HOST, (), now(), listed=listed
    )
    assert stored is None
    with factory() as db:
        written = sa.exists().where(JobEvent.data.has_key(service.PAGE_UNREAD))
        assert not db.scalar(sa.select(written))


def test_a_redirect_to_the_websites_search_page_leaves_the_posting_as_it_was(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Review of PR 864, item 3: the posting's address redirects to the website's search page,
    which lists its title among other cards: its address, its text and its mention stay."""
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"))
    [before] = jobs_of(factory, alice)
    results = START + "search?q=genai"
    listing = "\n".join(
        ['- heading "GenAI jobs near Paris" [level=1]']
        + [f'- heading "{t}" [level=3]' for t in ("GenAI engineer", "AI engineer", "ML engineer")]
        + [posting("GenAI engineer").split("\n", 1)[1]]
    )
    board = Board({results: listing}, start=START, redirects={before.url: results})
    row = search_with_pages(factory, alice, board)
    [after] = jobs_of(factory, alice)
    assert (after.url, after.posting_text, after.list_url) == (
        before.url, before.posting_text, LIST,
    )  # fmt: skip
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert (entry["listed_pages"]["read"], entry["listed_pages"]["unread"]) == (0, 1)
    assert entry["listed_read"] == 0


def closed_by_the_app_then_reopened(factory: sessionmaker[Session], job: Job) -> None:
    """The app closed the posting once (liveness), and the person opened it again."""
    with factory() as db, db.begin():
        stored = service.lock_job(db, job.user_id, job.id)
        assert stored is not None
        service.set_status(db, stored, JobStatus.closed, now(), via="liveness")
        service.set_status(db, stored, JobStatus.found, now())


def test_a_posting_the_person_reopened_after_the_app_closed_it_is_not_opened_again(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Review of PR 864, item 5: the person's reopen stands: the reading never opens its page
    again, never counts it closed, and it no longer waits for this reading."""
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"))
    [job] = jobs_of(factory, alice)
    closed_by_the_app_then_reopened(factory, job)
    board = Board({}, start=START, statuses={job.url: 404})
    row = search_with_pages(factory, alice, board)
    assert set(board.opened()) == {START}  # the agent's start page alone
    [after] = jobs_of(factory, alice)
    assert (after.status, after.list_url) == (JobStatus.found, LIST)
    [entry] = [s for s in row.stats["sources"] if s.get("host") == HOST]
    assert (entry["listed_pages"]["closed"], entry["listed_pages"]["left"]) == (0, 0)


def test_the_store_says_what_it_did_not_what_the_page_answered(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Review of PR 864, item 5: a gone page of a posting the app closed before and the person
    reopened (meanwhile) is not closed and is counted in nothing; a page read for a posting
    whose own page was read meanwhile writes nothing and is counted in nothing."""
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card("GenAI engineer"), card("AI engineer"))
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    reopened, read = jobs["GenAI engineer"], jobs["AI engineer"]
    closed_by_the_app_then_reopened(factory, reopened)
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == read.id).values(list_url=None))
    plan = sweep.claim(factory, new_sweep(factory, alice), now())
    assert plan is not None
    gone = liveness.Finding(liveness.CLOSED, "http_404")
    page = dataclasses.replace(finding("AI engineer"), text=posting("AI engineer"))
    for listed in (
        sweep.ListedPage(reopened.id, reopened.url, "gone", closed=gone),
        sweep.ListedPage(read.id, read.url, "read", page),
    ):
        stored = sweep.commit_verified(
            factory, plan, plan.known.copy(), sweep.Stats(), "s", HOST, (), now(), listed=listed
        )
        assert stored is not None
        assert (listed.done, listed.given_up) == ("", False)
        assert stored.listed_read == 0
    after = {job.title: job for job in jobs_of(factory, alice)}
    assert (after["GenAI engineer"].status, after["GenAI engineer"].list_url) == (
        JobStatus.found, LIST,
    )  # fmt: skip
    assert after["AI engineer"].posting_text == read.posting_text


def test_at_most_listed_max_postings_after_the_exact_filters(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch
) -> None:
    """Review of PR 864, follow ups 3 and 4: LISTED_MAX counts only the postings the website's
    reading opens (a source that only holds the host's name aside), and a card of this search
    found again under another posting's address comes first."""
    set_profile(factory, alice, search())
    titles = ("GenAI engineer", "AI engineer", "Forward deployed engineer")
    run(factory, new_sweep(factory, alice), *(card(t) for t in titles))
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    with factory() as db, db.begin():
        for hours, title in enumerate(titles):
            db.execute(
                sa.update(Job)
                .where(Job.id == jobs[title].id)
                .values(created_at=now() - timedelta(hours=3 - hours))
            )
        db.execute(  # the oldest: a source that holds the host's name, not the host
            sa.update(Job).where(Job.id == jobs[titles[0]].id).values(source=f"x{HOST}")
        )
    monkeypatch.setattr(sweep, "LISTED_MAX", 2)

    def on_site(url: str) -> bool:
        return finder.on_lease_hosts(url, START)

    picked = sweep.listed_postings(factory, alice, HOST, on_site, set())
    assert [p.title for p in picked] == [titles[1], titles[2]]
    assert sweep.listed_left(factory, alice, HOST, on_site) == 2
    plan = sweep.claim(factory, new_sweep(factory, alice), now())
    assert plan is not None
    again = dataclasses.replace(card(titles[2]), url=jobs[titles[2]].url + "?utm_source=list")
    known = plan.known.copy()
    sweep.commit_verified(factory, plan, known, sweep.Stats(), "s", HOST, [again], now())
    assert known.carded == {jobs[titles[2]].id}  # a duplicate, stored under the row's address
    picked = sweep.listed_postings(factory, alice, HOST, on_site, known.carded)
    assert [p.title for p in picked] == [titles[2], titles[1]]
