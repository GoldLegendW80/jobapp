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
        # The person's own rule leaves it out: never fetched, never stored.
        card("4100000023", "Cyber Security GenAI Engineer"),
    ]
)
OWNERS_RULE = "No cybersecurity roles: cyber, security, pentest, threat"


def run_sweep(
    sessions: sessionmaker[Session],
    user: uuid.UUID,
    rules: tuple[str, ...] = (OWNERS_RULE,),
    page: str = PAGE,
    **fields: Any,
) -> tuple[dict[str, Any], LinkedInFake]:
    search = section(never_apply=list(rules), **fields)
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
    plan = sweep.Plan(sweep_id=sweep_id, user_id=user, rules=sweep.never_apply_rules(search),
                      skills=None, known=known, search=search, search_scope=scope)  # fmt: skip
    plan.commits = sweep.Commits(sessions, plan, stats, lambda: datetime.now(UTC))
    now = [0.0]
    fake = LinkedInFake(now, pages={0: page})

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


def test_without_a_rule_of_the_persons_a_security_role_is_fetched_and_stored_found(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    """The app has no rule of its own about a kind of role (owner, 2026-11-05)."""
    entry, fake = run_sweep(factory, alice, rules=())
    assert fake.paths() == ["search?start=0", "4100000021", "4100000022", "4100000023"]
    assert (entry["found"], entry["postings"], entry.get("unwanted", 0)) == (3, 3, 0)
    with factory() as database:
        stored = database.scalars(
            sa.select(Job).where(Job.url == "https://www.linkedin.com/jobs/view/4100000023/")
        ).one()
    assert (stored.title, stored.status, stored.notes) == (
        "Cyber Security GenAI Engineer",
        JobStatus.found,
        "",
    )


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


def test_a_card_titled_outside_the_saved_terms_is_read_and_stored_and_another_place_is_not(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    """Jobs J20: the title rule moved after reading (a "Tech Lead" or a French title a search for
    the saved term returned is stored, and scoring judges it), and a posting of a place none of
    the saved ones is not stored (the sweep of 2026-11-17 stored postings of Miami for three
    French overseas places)."""
    page = "".join(
        [
            card("4100000031", "Tech Lead IA", place="Papeete, Windward Islands, French Polynesia"),
            card("4100000032", "Ingénieur IA générative", place="Saint-Denis, Réunion"),
            card("4100000033", "GenAI Engineer", place="Miami, FL"),
        ]
    )
    entry, fake = run_sweep(factory, alice, page=page, location={"terms": ["Tahiti", "La Réunion"]})
    assert entry["other_place"] == 1  # Miami: a card is judged once, whatever search gave it
    with factory() as database:
        titles = database.scalars(
            sa.select(Job.title).where(Job.user_id == alice).order_by(Job.url)
        ).all()
    assert titles == ["Tech Lead IA", "Ingénieur IA générative"]
    assert "4100000033" not in fake.paths()  # never fetched
    locations = [r.url.params["location"] for r in fake.requests if "location" in r.url.params]
    assert locations == ["French Polynesia", "Reunion"]


def test_an_internship_read_where_linkedin_could_not_filter_is_kept_with_its_badge(
    factory: sessionmaker[Session],  # noqa: F811
    alice: uuid.UUID,  # noqa: F811
) -> None:
    """Jobs J30 (owner, 2026-11-26: "Garder avec un badge"): Alternance has no LinkedIn code, so
    the search sends no f_JT and reads internships; they are stored, outside the saved contract
    types, and counted so."""
    entry, _ = run_sweep(factory, alice, contract_types=["CDI", "Alternance"])
    assert (entry["postings"], entry["outside"], "other_contract" in entry) == (2, 2, False)
    with factory() as database:
        rows = database.execute(
            sa.select(Job.contract_type, Job.contract_stated, Job.outside_criteria).where(
                Job.user_id == alice
            )
        ).all()
    assert sorted(map(tuple, rows)) == [(ContractType.Stage, True, ["contract"])] * 2
