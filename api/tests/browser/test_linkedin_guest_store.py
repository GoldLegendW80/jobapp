"""LinkedIn's public job search over real Postgres: what a sweep stores through the run's commits
(sweep.Commits), and the duplicates of a second sweep, never fetched again. Recorded fixtures,
an httpx.MockTransport: no network (app/browser/sources/linkedin_guest.py)."""

import asyncio
import random
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import sources
from app.jobs import sweep
from app.jobs.models import (
    ContractType,
    Job,
    JobEvent,
    JobEventKind,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
from tests.browser.test_linkedin_guest import POSTING, Allow, LinkedInFake, card, section
from tests.jobs.conftest import (  # noqa: F401  (fixtures)
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
)

PAGE = "".join(
    [
        card("4100000021", "GenAI Engineer"),
        card("4100000022", "GenAI Engineer", company="Example Labs", place="Lyon, France"),
        card("4100000023", "Cyber Security GenAI Engineer"),  # never fetched, never stored
    ]
)


def run_sweep(
    sessions: sessionmaker[Session], user: uuid.UUID
) -> tuple[dict[str, Any], LinkedInFake]:
    search = section()
    with sessions() as database:
        row = Sweep(user_id=user, trigger=SweepTrigger.manual, status=SweepStatus.running,
                    stats={})  # fmt: skip
        database.add(row)
        database.commit()
        sweep_id = row.id
        known = sweep.Known(database, user)
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=1),
        SweepTrigger.manual,
        datetime(2026, 10, 30, tzinfo=UTC),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = sweep.Plan(sweep_id=sweep_id, user_id=user, rules=[], skills=None, known=known,
                      search=search, search_scope=scope)  # fmt: skip
    plan.commits = sweep.Commits(sessions, plan, stats, lambda: datetime.now(UTC))
    now = [0.0]
    fake = LinkedInFake(now, pages={0: PAGE})

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    async def progress() -> None:
        return None

    async def go() -> None:
        runner = sources.Runner(
            None,  # type: ignore[arg-type]  no lease is ever asked for
            lambda *_: None,
            sleep=sleep,
            monotonic=lambda: now[0],
            rng=random.Random(3),  # noqa: S311  deterministic test pacing
            pacer=sources.MemoryPacer(lambda: now[0]),
            interactive_policy=Allow(),
            guest_http=lambda: httpx.MockTransport(fake.handler),
        )
        await runner(plan, stats, progress, 1000.0)
        assert await plan.commits.settle(close=True)

    asyncio.run(go())
    with sessions() as database, database.begin():
        stored = database.get(Sweep, sweep_id)
        assert stored is not None
        stored.status = SweepStatus.done  # one running sweep per person
        stored.finished_at = datetime.now(UTC)
    [entry] = stats.sources
    return entry, fake


def test_postings_are_stored_scored_and_dated_with_the_contract_linkedin_states(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    entry, fake = run_sweep(factory, alice)
    assert fake.paths() == ["search?start=0", "4100000021", "4100000022"]
    assert (entry["found"], entry["postings"], entry["error"], entry["outcome"]) == (
        2,
        2,
        None,
        "complete",
    )
    assert entry["unwanted"] == 1
    with factory() as database:
        jobs = database.scalars(sa.select(Job).where(Job.user_id == alice).order_by(Job.url)).all()
        events = database.scalars(
            sa.select(JobEvent.data).where(JobEvent.kind == JobEventKind.sweep)
        ).all()
        texts = [j.posting_text for j in jobs]
    assert [(j.url, j.company, j.location, j.source) for j in jobs] == [
        ("https://www.linkedin.com/jobs/view/4100000021/", "Example Robotics", "Paris, France",
         "www.linkedin.com"),
        ("https://www.linkedin.com/jobs/view/4100000022/", "Example Labs", "Lyon, France",
         "www.linkedin.com"),
    ]  # fmt: skip
    job = jobs[0]
    assert job.title == "GenAI Engineer"
    assert job.status is JobStatus.found
    assert job.contract_type is ContractType.Stage  # "Employment type: Internship"
    assert job.published_on == date(2026, 10, 29)
    assert texts[0] is not None
    assert texts[0].startswith("About the role")
    assert "Employment type: Internship" in texts[0]
    assert all(e["discovery"]["evidence"]["read_through"] == "linkedin_guest_api" for e in events)
    assert POSTING not in str(events)


def test_a_second_sweep_counts_the_same_postings_as_duplicates_without_fetching_them(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
    bob: uuid.UUID,  # noqa: F811
) -> None:
    run_sweep(factory, alice)
    entry, fake = run_sweep(factory, alice)
    assert fake.paths() == ["search?start=0"]
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 0, 2)
    with factory() as database:
        assert database.scalar(sa.select(sa.func.count()).select_from(Job)) == 2
    # Another person's postings are never this person's duplicates.
    entry, fake = run_sweep(factory, bob)
    assert entry["postings"] == 2
    assert len(fake.requests) == 3
