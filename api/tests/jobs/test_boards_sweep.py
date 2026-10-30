"""Company boards in a sweep (app/jobs/boards/run.py), against real Postgres, the ATSs replaced
by httpx's MockTransport over the hand-made fixtures: what is stored and how, the frozen scope
and the stats entries, duplicates, a 429, the boards' time share, a search with boards only, and
the worker's chain (boards first, then the websites)."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.jobs import sweep
from app.jobs.boards import run as boards
from app.jobs.models import ContractType, Job, JobEvent, JobStatus, Sweep, SweepStatus
from tests.jobs.sweep_helpers import (
    SITE,
    finding,
    jobs_of,
    new_sweep,
    read_sweep,
    search,
    set_profile,
    websites,
)

FIXTURES = Path(__file__).with_name("boards_fixtures")
CLOCK = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)  # the fixtures' dates are a week before
ROBOTICS = {"company": "Example Robotics", "ats": "ashby", "board": "example-robotics"}
LABS = {"company": "Example Labs", "ats": "greenhouse", "board": "examplelabs"}
WORKS = {"company": "Example Works", "ats": "lever", "board": "exampleworks"}


def clock() -> datetime:
    return CLOCK


def fixture_answers(
    seen: list[str] | None = None, status: dict[str, int] | None = None
) -> httpx.MockTransport:
    names = {
        "api.ashbyhq.com": "ashby",
        "boards-api.greenhouse.io": "greenhouse",
        "api.lever.co": "lever",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.url.host}{request.url.path}")
        code = (status or {}).get(request.url.host, 200)
        if code != 200:
            return httpx.Response(code)
        name = names[request.url.host]
        return httpx.Response(200, content=(FIXTURES / f"{name}.json").read_bytes())

    return httpx.MockTransport(handler)


async def no_wait(_: float) -> None:
    return None


def hook(transport: httpx.MockTransport, **options: Any) -> boards.Boards:
    return boards.Boards(transport, sleep=no_wait, **options)


def sweep_with(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    the_boards: list[dict[str, Any]],
    transport: httpx.MockTransport,
    then: sweep.BrowserHook | None = None,
    **overrides: Any,
) -> tuple[uuid.UUID, dict[str, Any]]:
    overrides.setdefault("interactive_sites", [])
    set_profile(factory, user, search(company_boards=the_boards, **overrides))
    sweep_id = new_sweep(factory, user)
    chained = boards.chain(hook(transport), then)
    asyncio.run(sweep.run_sweep(factory, sweep_id, clock, browser=chained))
    return sweep_id, read_sweep(factory, sweep_id).stats


def boards_of(stats: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in stats["sources"] if s.get("mode") == "api"]


def test_a_board_is_read_filtered_and_stored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    seen: list[str] = []
    sweep_id, stats = sweep_with(factory, alice, [ROBOTICS], fixture_answers(seen))
    assert read_sweep(factory, sweep_id).status is SweepStatus.done
    assert seen == ["api.ashbyhq.com/posting-api/job-board/example-robotics"]  # one request
    jobs = {j.title: j for j in jobs_of(factory, alice)}
    # Kept: the Paris posting, the remote intern (no contract saved), and the security one,
    # stored skipped by the person's never apply rule; left out: an unwanted title, Tokyo, an
    # old date; the unlisted one is not read at all.
    assert set(jobs) == {"Forward Deployed Engineer", "AI Engineer Intern", "Security AI Engineer"}
    fde = jobs["Forward Deployed Engineer"]
    assert (fde.company, fde.source, fde.status) == (
        "Example Robotics", "ashby:example-robotics", JobStatus.found,
    )  # fmt: skip
    assert (fde.location, fde.published_on) == ("Paris, London", date(2026, 10, 1))
    assert fde.url == "https://jobs.ashbyhq.com/example-robotics/1111"
    assert fde.fit is not None
    assert fde.posting_text.startswith("Forward Deployed Engineer")
    assert jobs["Security AI Engineer"].status is JobStatus.skipped
    # Ashby's FullTime is a schedule, so a title-neutral posting is a CDI; its Intern a Stage.
    assert fde.contract_type is ContractType.CDI
    assert jobs["AI Engineer Intern"].contract_type is ContractType.Stage
    [entry] = boards_of(stats)
    assert entry == entry | {
        "mode": "api",
        "kind": "board",
        "host": "api.ashbyhq.com",
        "query": "ashby:example-robotics",
        "company": "Example Robotics",  # frozen with the scope: the summary names it
        "phase": "done",
        "outcome": "complete",
        "reason": None,
        "result_pages": 1,
        "listed": 6,
        "found": 3,
        "postings": 3,
        "skipped_by_rule": 1,
        "left_out": {"excluded": 1, "location": 1, "too_old": 1},
    }
    # Seen: every posting listed; matched and new: what was stored; invalid: no address or not
    # an object.
    assert (stats["seen"], stats["matched"], stats["new"], stats["invalid"]) == (6, 3, 3, 2)
    with factory() as db:
        event = db.execute(
            sa.select(JobEvent).where(JobEvent.job_id == fde.id, JobEvent.kind == "sweep")
        ).scalar_one()
    assert event.data["board"] == "ashby:example-robotics"
    assert event.data["discovery"]["source_id"] == entry["source_id"]
    assert event.data["discovery"]["evidence"] == {}


def test_the_frozen_scope_holds_one_source_per_enabled_board(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    off = {**WORKS, "enabled": False}
    set_profile(factory, alice, search(company_boards=[ROBOTICS, LABS, off]))
    sweep_id = new_sweep(factory, alice)
    stats = read_sweep(factory, sweep_id).stats
    scoped = [s for s in stats["search_scope"]["sources"] if s.get("mode") == "api"]
    assert [s["query"] for s in scoped] == ["ashby:example-robotics", "greenhouse:examplelabs"]
    assert [s["company"] for s in scoped] == ["Example Robotics", "Example Labs"]
    assert [s["phase"] for s in boards_of(stats)] == ["pending", "pending"]
    assert [s["company"] for s in boards_of(stats)] == ["Example Robotics", "Example Labs"]
    # The website stays a source of its own beside them.
    assert [s["host"] for s in stats["sources"] if s.get("mode") == "llm_browser"] == [
        "jobs.example-robotics.test"
    ]


def test_no_saved_board_is_never_frozen_into_the_scope(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """The release before the boards refuses the key: a run frozen without boards stays one it
    can claim after a rollback (SPEC-jobs, Company job boards, the one way door)."""
    for saved in ({}, {"company_boards": None}, {"company_boards": []}):
        set_profile(factory, alice, search(**saved))
        sweep_id = new_sweep(factory, alice)
        frozen = read_sweep(factory, sweep_id).stats["search_scope"]["search"]
        assert "company_boards" not in frozen, saved
        with factory() as db, db.begin():
            row = db.get(Sweep, sweep_id)
            row.status, row.finished_at = SweepStatus.done, CLOCK
    set_profile(factory, alice, search(company_boards=[LABS]))
    frozen = read_sweep(factory, new_sweep(factory, alice)).stats["search_scope"]["search"]
    assert frozen["company_boards"] == [{**LABS, "enabled": True}]


INTERNSHIP_FULL_TIME = {
    "jobs": [
        {
            "title": "AI Engineer, Internship",
            "location": "Paris",
            "employmentType": "FullTime",
            "publishedAt": "2026-10-04T10:00:00.000+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/example-robotics/9001",
            "descriptionPlain": "An internship listed FullTime by its ATS.",
        },
        {
            "title": "AI Engineer",
            "location": "Paris",
            "employmentType": "FullTime",
            "publishedAt": "2026-10-04T10:00:00.000+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/example-robotics/9002",
            "descriptionPlain": "A permanent role, nothing in the title.",
        },
    ]
}


@pytest.mark.parametrize(
    ("wanted", "stored"),
    [
        (["Stage"], {"AI Engineer, Internship": ContractType.Stage}),
        (["CDI"], {"AI Engineer": ContractType.CDI}),
        ([], {"AI Engineer, Internship": ContractType.Stage, "AI Engineer": ContractType.CDI}),
    ],
)
def test_one_contract_per_posting_filters_and_is_stored(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    wanted: list[str],
    stored: dict[str, ContractType],
) -> None:
    """The title's "Internship" beats the ATS's FullTime, in the filter and in the stored job."""
    answer = httpx.MockTransport(lambda _: httpx.Response(200, json=INTERNSHIP_FULL_TIME))
    _, stats = sweep_with(factory, alice, [ROBOTICS], answer, contract_types=wanted)
    assert {j.title: j.contract_type for j in jobs_of(factory, alice)} == stored
    [entry] = boards_of(stats)
    assert entry["left_out"] == ({"contract": 1} if wanted else {})


def test_boards_only_is_a_search(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    set_profile(factory, alice, search(interactive_sites=[], company_boards=[LABS]))
    with factory() as db:
        assert sweep.has_sources(sweep.load_search(db, alice))
    set_profile(factory, alice, search(interactive_sites=[], company_boards=[]))
    with factory() as db:
        assert not sweep.has_sources(sweep.load_search(db, alice))


def test_duplicates_across_boards_and_with_the_persons_list(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    sweep_with(factory, alice, [LABS], fixture_answers())
    before = len(jobs_of(factory, alice))
    # The same board twice is refused by the profile; a second sweep finds only duplicates.
    _, stats = sweep_with(factory, alice, [LABS], fixture_answers())
    assert len(jobs_of(factory, alice)) == before == 2
    [entry] = boards_of(stats)
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 0, 2)
    assert entry["outcome"] == "complete"


def test_a_board_found_by_a_website_too_is_one_job_naming_both(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    same = finding(
        "AI Engineer",
        company="Example Labs",
        location="London, UK",
        url="https://job-boards.greenhouse.io/examplelabs/jobs/42",
    )
    sweep_with(
        factory, alice, [LABS], fixture_answers(), then=websites(same), interactive_sites=[SITE]
    )
    jobs = [j for j in jobs_of(factory, alice) if j.title == "AI Engineer"]
    assert len(jobs) == 1
    assert jobs[0].source == "greenhouse:examplelabs, jobs.example-robotics.test"


def test_a_429_stops_the_ats_and_leaves_the_others_running(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    seen: list[str] = []
    other = {"company": "Example Two", "ats": "ashby", "board": "example-two"}
    _, stats = sweep_with(
        factory,
        alice,
        [ROBOTICS, other, LABS],
        fixture_answers(seen, status={"api.ashbyhq.com": 429}),
    )
    first, second, labs = boards_of(stats)
    assert (first["outcome"], first["reason"]) == ("failed", "rate_limited")
    assert (second["outcome"], second["reason"]) == ("not_attempted", "host_rate_limited")
    assert (labs["outcome"], labs["postings"]) == ("complete", 2)
    assert seen.count("api.ashbyhq.com/posting-api/job-board/example-robotics") == 1
    assert not any("example-two" in s for s in seen)


def test_a_board_that_fails_ends_alone(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    _, stats = sweep_with(
        factory, alice, [WORKS, LABS], fixture_answers(status={"api.lever.co": 404})
    )
    works, labs = boards_of(stats)
    assert (works["outcome"], works["reason"]) == ("failed", "not_found")
    assert labs["outcome"] == "complete"


def test_without_a_wanted_title_no_board_is_read(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    seen: list[str] = []
    _, stats = sweep_with(
        factory,
        alice,
        [LABS],
        fixture_answers(seen),
        title_include={"terms": [], "advanced_regex": False},
    )
    assert seen == []
    [entry] = boards_of(stats)
    assert (entry["outcome"], entry["reason"]) == ("not_attempted", "no_titles")


def test_the_boards_keep_to_their_share_of_the_sweep(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(interactive_sites=[], company_boards=[LABS, WORKS]))
    sweep_id = new_sweep(factory, alice)
    now = [0.0]

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()  # Lever never answers
        raise AssertionError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=(FIXTURES / "greenhouse.json").read_bytes())

    async def routed(request: httpx.Request) -> httpx.Response:
        return await hang(request) if request.url.host == "api.lever.co" else handler(request)

    late = boards.Boards(
        httpx.MockTransport(routed), sleep=no_wait, monotonic=lambda: now[0], budget_s=300
    )

    async def run(plan: Any, stats: sweep.Stats, progress: Any, deadline: float) -> None:
        async def moved() -> None:
            await progress()
            if any(s.get("query") == "greenhouse:examplelabs" and s.get("phase") == "done"
                   for s in stats.sources):  # fmt: skip
                now[0] = 400.0  # the boards' 300 s are used once the first board is stored

        await late(plan, stats, moved, deadline)

    asyncio.run(sweep.run_sweep(factory, sweep_id, clock, browser=run))
    labs, works = boards_of(read_sweep(factory, sweep_id).stats)
    assert labs["outcome"] == "complete"
    assert (works["outcome"], works["reason"]) == ("not_attempted", "time_budget")


def test_the_worker_runs_the_boards_then_the_websites(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    order: list[str] = []
    transport = fixture_answers()

    def recorded(name: str, inner: Callable[..., Any]) -> Callable[..., Any]:
        async def run(*args: Any) -> None:
            order.append(name)
            await inner(*args)

        return run

    set_profile(factory, alice, search(company_boards=[LABS]))
    sweep_id = new_sweep(factory, alice)
    ctx = {
        "sessions": factory,
        "clock": clock,
        "boards_transport": transport,
        # No alert e-mail is on (and the context has no settings for the Gmail hook).
        "alert_sources": None,
        "browser_sources": recorded("websites", websites(finding("GenAI Engineer"))),
    }
    asyncio.run(worker.run_sweep(ctx, str(sweep_id)))
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    assert [s["mode"] for s in row.stats["sources"]] == ["llm_browser", "api"]
    assert order == ["websites"]
    with factory() as db:
        sources = set(db.execute(sa.select(Job.source).where(Job.user_id == alice)).scalars())
    assert sources == {"greenhouse:examplelabs", "jobs.example-robotics.test"}
