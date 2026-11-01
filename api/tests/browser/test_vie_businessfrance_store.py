"""Business France VIE's public search API over real Postgres: what a sweep stores through the
run's commits (sweep.Commits), and the same offers on a second sweep, duplicates. Recorded
fixtures, an httpx.MockTransport: no network (app/browser/sources/vie_businessfrance.py)."""

import asyncio
import random
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import sources
from app.browser.sources import vie_businessfrance as vie
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
from tests.browser.test_vie_businessfrance import NOW, SEARCH, VieFake, page_of, section
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

CYBER = {**SEARCH["result"][1], "id": 900020, "missionTitle": "Cyber Security Data Engineer"}
PAGES = {
    ("genai engineer", 0): page_of([SEARCH["result"][0], SEARCH["result"][2]]),
    ("data engineer", 0): page_of([SEARCH["result"][1], CYBER]),
}


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vie, "now", lambda: NOW)


def run_sweep(sessions: sessionmaker[Session], user: uuid.UUID) -> tuple[dict[str, Any], VieFake]:
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
        NOW,
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = sweep.Plan(sweep_id=sweep_id, user_id=user, rules=sweep.never_apply_rules(search),
                      skills=None, known=known, search=search, search_scope=scope)  # fmt: skip
    plan.commits = sweep.Commits(sessions, plan, stats, lambda: datetime.now(UTC))
    now = [0.0]
    fake = VieFake(now, PAGES)

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
            guest_http=lambda: httpx.MockTransport(fake.handler),
        )
        await runner.vie(plan, stats, progress, 1000.0)
        assert await plan.commits.settle(close=True)

    asyncio.run(go())
    with sessions() as database, database.begin():
        stored = database.get(Sweep, sweep_id)
        assert stored is not None
        stored.status = SweepStatus.done  # one running sweep per person
        stored.finished_at = datetime.now(UTC)
    [entry] = stats.sources
    return entry, fake


def test_offers_are_stored_as_vie_postings_with_the_employer_address_kept(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    entry, fake = run_sweep(factory, alice)
    assert len(fake.requests) == 3  # the key page and one page of each title
    assert (entry["found"], entry["postings"], entry["error"], entry["outcome"]) == (
        3,
        3,
        None,
        "complete",
    )
    assert entry["left_out"] == {"title": 1}
    with factory() as database:
        jobs = database.scalars(sa.select(Job).where(Job.user_id == alice).order_by(Job.url)).all()
        events = database.scalars(
            sa.select(JobEvent.data).where(JobEvent.kind == JobEventKind.sweep)
        ).all()
        texts = [j.posting_text or "" for j in jobs]
    assert [(j.url, j.company, j.location, j.source, j.status) for j in jobs] == [
        ("https://mon-vie-via.businessfrance.fr/offres/900001", "Example Aero",
         "Munich, Germany", "mon-vie-via.businessfrance.fr", JobStatus.found),
        ("https://mon-vie-via.businessfrance.fr/offres/900002", "Example Logistics",
         "Charleston -Sc-, United States", "mon-vie-via.businessfrance.fr", JobStatus.found),
        # The app's own rule against cybersecurity marks it skipped, as for every source.
        ("https://mon-vie-via.businessfrance.fr/offres/900020", "Example Logistics",
         "Charleston -Sc-, United States", "mon-vie-via.businessfrance.fr", JobStatus.skipped),
    ]  # fmt: skip
    assert all(j.contract_type is ContractType.VIE for j in jobs)
    assert jobs[0].published_on == date(2026, 10, 28)
    assert "Build LLM agents" in texts[0]
    evidence = {e["discovery"]["evidence"]["external_id"]: e["discovery"]["evidence"]
                for e in events}  # fmt: skip
    assert evidence["900001"]["apply_url"] == "https://careers.example-aero.test/jobs/123"
    assert "apply_url" not in evidence["900002"]
    assert all(e["read_through"] == "vie_businessfrance_api" for e in evidence.values())
    stored = str(texts) + str(events)
    for private in ("Jane", "Doe", "John", "Roe", "e2e-recruiter", "1234 5678"):
        assert private not in stored


def test_a_second_sweep_finds_the_same_offers_as_duplicates(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    run_sweep(factory, alice)
    entry, _ = run_sweep(factory, alice)
    assert (entry["found"], entry["postings"], entry.get("duplicates")) == (3, 0, 3)
    with factory() as database:
        count = database.scalar(sa.select(sa.func.count()).where(Job.user_id == alice))
    assert count == 3
