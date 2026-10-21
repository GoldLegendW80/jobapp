"""Browser B8: a sweep runs the person's browser sources after the boards, and their postings go
through the sweep's own store (status found, scored, never apply rules, duplicates, the posting
text saved, one person's rows only). Real Postgres; the browser is the fake tools and lease store
of tests/browser/test_b8_sources.py, on snapshots of invented postings."""

import random
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.browser.sources import MemoryPacer, Runner
from app.browser.sources import google_careers as gc
from app.jobs import sweep, sweep_router
from app.jobs.models import JobStatus, SweepStatus
from tests.browser import b8_pages as pages
from tests.browser.test_b8_sources import FakeLeases, FakeTools
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import (
    board,
    jobs_of,
    new_sweep,
    run,
    search,
    set_profile,
)


@pytest.fixture
def fake_ats() -> Iterator[FakeAts]:
    with FakeAts() as server:
        yield server


SOURCE = {"kind": "google_careers", "query": "ai", "enabled": True}


def site(*made: pages.Offer) -> dict[str, str]:
    url = gc.results_url(sweep_source(), 1)
    found = {url: pages.google_results(list(made))}
    for o in made:
        found[f"{gc.RESULTS}{o.number}-{pages.slug(o.title)}"] = pages.google_posting(o)
    return found


def sweep_source() -> Any:
    from app.profile.schemas import BrowserSource

    return BrowserSource.model_validate(SOURCE)


def runner(tools: FakeTools, leases: FakeLeases | None = None) -> Runner:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Runner(
        leases or FakeLeases(),  # type: ignore[arg-type]
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=random.Random(1),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
    )


def offer(title: str, company: str = "Example Robotics", number: int = 1) -> pages.Offer:
    return pages.Offer(title=title, company=company, location="Paris, France", number=number)


def test_browser_postings_are_stored_scored_and_ruled(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    made = (
        offer("AI Engineer", number=1),
        offer("GenAI Security Engineer", number=2),  # the never apply rule of the profile
        offer("Account Executive", number=3),  # title_exclude, as the API sweep does
    )
    set_profile(factory, alice, search(browser_sources=[SOURCE]))
    leases = FakeLeases()
    row = run(
        factory, new_sweep(factory, alice), fake_ats, browser=runner(FakeTools(site(*made)), leases)
    )
    assert (row.status, row.error) == (SweepStatus.done, None)
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    assert set(jobs) == {"AI Engineer", "GenAI Security Engineer"}
    kept = jobs["AI Engineer"]
    assert kept.status is JobStatus.found
    assert kept.source == "google_careers:ai"
    assert kept.company == "Example Robotics"
    assert kept.fit is not None
    assert kept.posting_text is not None
    assert "Build agents with Python" in kept.posting_text
    assert kept.url == f"{gc.RESULTS}1-ai-engineer"
    assert jobs["GenAI Security Engineer"].status is JobStatus.skipped
    assert row.stats["sources"][0]["error"] is None
    assert row.stats["sources"][0]["postings"] == 2
    assert row.stats["sources"][0]["found"] == 3  # read completely; the excluded title is not new
    out = sweep_router.sweep_out(row).sources
    assert [(s.kind, s.query, s.found, s.new, s.error) for s in out] == [
        ("google_careers", "ai", 3, 2, None)
    ]
    assert leases.acquired[0][0] == str(alice)


def test_a_posting_the_api_found_is_no_new_row(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok", "ashby", "Acme AI")))
    run(factory, new_sweep(factory, alice), fake_ats)
    before = len(jobs_of(factory, alice))
    # The board's posting (same company, title and location), now listed by a browser source.
    same = pages.google_results([offer("AI Engineer", "Acme AI", 1)])
    tools = FakeTools({gc.results_url(sweep_source(), 1): same})
    set_profile(
        factory, alice, search(board("fake-ok", "ashby", "Acme AI"), browser_sources=[SOURCE])
    )
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=runner(tools))
    assert row.status is SweepStatus.done
    assert len(jobs_of(factory, alice)) == before
    assert len(tools.calls) == 1  # the known posting's page is not even opened


def test_a_changed_layout_stores_nothing_and_the_sweep_is_done(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    broken = {
        k: v.replace("Jobs search results", "Something else")
        for k, v in site(offer("AI Engineer")).items()
    }
    set_profile(
        factory, alice, search(board("fake-ok", "ashby", "Acme AI"), browser_sources=[SOURCE])
    )
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=runner(FakeTools(broken)))
    assert row.status is SweepStatus.done
    assert row.stats["sources"][0]["error"] == "layout_changed"
    assert row.stats["sources"][0]["found"] == 0  # nothing of a changed page is kept
    assert sweep_router.sweep_out(row).sources[0].error == "layout_changed"
    assert all(job.source != "google_careers:ai" for job in jobs_of(factory, alice))
    assert row.stats["boards_ok"] == 1  # the boards' postings are kept


def test_one_persons_sweep_never_writes_anothers_postings(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(browser_sources=[SOURCE]))
    set_profile(factory, bob, search(browser_sources=[SOURCE]))
    leases = FakeLeases()
    run(
        factory,
        new_sweep(factory, bob),
        fake_ats,
        browser=runner(FakeTools(site(offer("AI Engineer"))), leases),
    )
    assert jobs_of(factory, alice) == []
    assert [job.title for job in jobs_of(factory, bob)] == ["AI Engineer"]
    assert {user for user, _, _ in leases.acquired} == {str(bob)}


def test_sources_alone_start_a_sweep_and_disabled_ones_do_not(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(browser_sources=[{**SOURCE, "enabled": False}]))
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=runner(FakeTools({})))
    assert (row.status, row.error) == (SweepStatus.failed, sweep.NO_BOARDS)
    linked = {**SOURCE, "kind": "linkedin"}  # B10's: enabled, never accepted: not planned
    set_profile(factory, alice, search(browser_sources=[linked]))
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=runner(FakeTools({})))
    assert row.error == sweep.NO_BOARDS


# --- round 2: a failure of one source never fails the sweep -------------------------------------


@pytest.mark.parametrize("failure", ["reader_bug", "store_gone", "store_failed"])
def test_a_source_failure_is_reported_and_the_sweep_is_done(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    tools = FakeTools(site(offer("AI Engineer")))
    if failure == "reader_bug":

        def odd(root: Any, page_url: str) -> Any:
            raise ValueError("invalid literal for int() with base 10: ''")

        monkeypatch.setattr(gc, "parse_results", odd)
    elif failure == "store_gone":
        tools.store_gone_after = 0
    else:

        def broken(*args: Any) -> None:
            raise OperationalError("INSERT", {}, Exception("the database went away"))

        monkeypatch.setattr(sweep, "_store_found", broken)
    set_profile(
        factory, alice, search(board("fake-ok", "ashby", "Acme AI"), browser_sources=[SOURCE])
    )
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=runner(tools))
    assert (row.status, row.error) == (SweepStatus.done, None)
    expected = {
        "reader_bug": "layout_changed",
        "store_gone": "browser_unavailable",
        "store_failed": "store_failed",
    }[failure]
    assert row.stats["sources"][0]["error"] == expected
    assert row.stats["boards_ok"] == 1
    jobs = jobs_of(factory, alice)
    assert jobs  # the boards' rows are kept
    assert all(job.source != "google_careers:ai" for job in jobs)
