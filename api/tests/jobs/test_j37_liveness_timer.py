"""The worker's timer of the liveness check (jobs J37, app/jobs/liveness_timer.py): which open
postings it asks and in what order, its bounds over a tick, a day, restarts and two workers, a
host that turned a check away left alone, a closing date that passed, a posting the person
reopened, LinkedIn never asked. Real Postgres, the liveness fakes of test_liveness_check (no
request leaves the test); every site and address is invented."""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser.sources import MemoryPacer
from app.config import Settings
from app.jobs import liveness_check, liveness_timer
from app.jobs import service as jobs_service
from app.jobs.liveness import CLOSED, Finding
from app.jobs.liveness_timer import Bounds
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus, Sweep, SweepStatus, SweepTrigger
from tests.jobs.conftest import new_user
from tests.jobs.test_liveness_check import COPY, Page, html, instant, resolver

NOW = datetime(2026, 11, 28, 9, 30, tzinfo=UTC)
OPEN_PAGE = f"<html><body><h1>Robotics engineer</h1><p>{COPY}</p></body></html>"
GONE_PAGE = f"<html><body><p>{COPY}</p><h2>This job is no longer available.</h2></body></html>"


class Web:
    """The fake web of the timer: every host answers its posting paths with `pages` (an open
    page unless named), and records each request in order."""

    def __init__(self, pages: dict[str, Page] | None = None) -> None:
        self.pages = pages or {}
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        asked = request.headers["host"] + request.url.raw_path.decode()
        self.seen.append(asked)
        page = self.pages.get(asked, html(OPEN_PAGE))
        return httpx.Response(page.status, headers=page.headers, stream=httpx.ByteStream(page.body))


def ctx(factory: sessionmaker[Session], web: Any, now: datetime, **bounds: int) -> dict[str, Any]:
    """A fresh worker's context: its own checker and pacer, nothing kept from an earlier one."""
    settings = SimpleNamespace(
        jobs_liveness_checks_per_day=bounds.get("per_day", 150),
        jobs_liveness_checks_per_tick=bounds.get("per_tick", 10),
        jobs_liveness_host_rest_hours=bounds.get("rest_hours", 24),
        jobs_liveness_again_days=bounds.get("again_days", 7),
    )
    checker = liveness_check.Checker(
        MemoryPacer(), transport=httpx.MockTransport(web), resolve=resolver(), sleep=instant
    )
    return {"sessions": factory, "clock": lambda: now, "settings": settings, "liveness": checker}


def tick(factory: sessionmaker[Session], web: Any, now: datetime = NOW, **bounds: int) -> int:
    return asyncio.run(liveness_timer.tick(ctx(factory, web, now, **bounds)))


def checked(at: datetime, reason: str = "none", verdict: str = "no_closure") -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reason": reason,
        "sentence": None,
        "checked_at": at.isoformat(),
        "moved": False,
    }


def add(
    factory: sessionmaker[Session], user: uuid.UUID, rows: list[dict[str, Any]]
) -> list[uuid.UUID]:
    """Postings as given: `host` and `n` name the address https://<host>/careers/<n>."""
    made = []
    with factory() as db, db.begin():
        for values in rows:
            values = dict(values)
            n = values.pop("n", len(made))
            host = values.pop("host", f"jobs{n}.example-robotics.example")
            job = Job(
                user_id=user,
                slug=f"j37-{uuid.uuid4().hex[:12]}",
                company="Example Robotics",
                title=f"Robotics engineer {n}",
                url=values.pop("url", f"https://{host}/careers/{n}"),
                found_on=values.pop("found_on", date(2026, 11, 1)),
                created_at=NOW - timedelta(days=30),
                updated_at=NOW - timedelta(days=30),
                **values,
            )
            db.add(job)
            db.flush()
            made.append(job.id)
    return made


def path(host: str, n: int) -> str:
    return f"{host}/careers/{n}"


def jobs(factory: sessionmaker[Session], ids: list[uuid.UUID]) -> list[Job]:
    with factory() as db:
        found = {job.id: job for job in db.scalars(sa.select(Job).where(Job.id.in_(ids)))}
    return [found[job_id] for job_id in ids]


def status_events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(JobEvent.data)
                .where(JobEvent.job_id == job_id, JobEvent.kind == JobEventKind.status)
                .order_by(JobEvent.id)
            )
        )


@pytest.fixture
def ann(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_user(factory, "e2e-j37-ann@example.org")


@pytest.fixture
def ben(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_user(factory, "e2e-j37-ben@example.org")


def host(n: int) -> str:
    return f"jobs{n}.example-robotics.example"


# --- the order and what is not the timer's -------------------------------------------------------


def test_never_checked_first_then_published_longest_ago_then_the_oldest_check(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    add(
        factory,
        ann,
        [
            {"n": 1, "published_on": date(2026, 10, 1)},
            {"n": 2, "published_on": None, "found_on": date(2026, 9, 1)},
            {"n": 3, "published_on": date(2026, 8, 1)},
            {
                "n": 4,
                "published_on": date(2026, 7, 1),
                "liveness": checked(NOW - timedelta(days=9)),
            },
            {
                "n": 5,
                "published_on": date(2026, 7, 1),
                "liveness": checked(NOW - timedelta(days=20)),
            },
            {
                "n": 6,
                "published_on": date(2026, 6, 1),
                "liveness": checked(NOW - timedelta(days=2)),
            },
        ],
    )
    web = Web()
    assert tick(factory, web) == 5
    assert web.seen == [path(host(n), n) for n in (3, 2, 1, 5, 4)]


def test_only_found_and_shortlisted_postings_with_an_address_are_asked(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    statuses = [s for s in JobStatus if s not in (JobStatus.found, JobStatus.shortlisted)]
    add(factory, ann, [{"n": 10 + i, "status": s} for i, s in enumerate(statuses)])
    add(factory, ann, [{"n": 1, "status": JobStatus.found, "url": None}])
    add(factory, ann, [{"n": 2, "status": JobStatus.shortlisted}])
    web = Web()
    assert tick(factory, web) == 1
    assert web.seen == [path(host(2), 2)]


def test_linkedin_postings_are_never_asked_nor_counted(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"url": "https://www.linkedin.com/jobs/view/4012345678"}])
    add(factory, ann, [{"n": 7, "url": "https://fr.linkedin.com/jobs/view/4012345679"}])
    add(factory, ann, [{"n": 8}])
    web = Web()
    assert tick(factory, web, per_tick=1) == 1
    assert web.seen == [path(host(8), 8)]
    assert jobs(factory, [posting])[0].liveness is None


def test_a_posting_that_left_the_open_ones_after_the_plan_is_not_asked_nor_closed(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1}])
    with factory() as db:
        job = jobs_service.lock_job(db, ann, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.cv_ready, NOW)
        db.commit()
        # The planned check finds it acted on: no request.
    web = Web()
    pick = liveness_timer.Pick(ann, posting, f"https://{host(1)}/careers/1", host(1))
    checker = ctx(factory, web, NOW)["liveness"]
    assert asyncio.run(liveness_timer._check(checker, factory, pick, lambda: NOW)) is False
    assert web.seen == []
    # A finding that lands once the person acted is stored, the posting left as she set it.
    with factory() as db:
        liveness_check.record(
            db,
            ann,
            posting,
            Finding(CLOSED, "http_404"),
            NOW,
            pick.url,
            close_from=liveness_timer.OPEN,
        )
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.cv_ready
    assert job.liveness is not None
    assert job.liveness["verdict"] == CLOSED
    assert job.liveness["moved"] is False


# --- what a check does ---------------------------------------------------------------------------


def test_a_closed_page_closes_the_posting_with_its_history_event(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    gone, said, still = add(factory, ann, [{"n": 1}, {"n": 2}, {"n": 3}])
    web = Web({path(host(1), 1): Page(404, b"gone"), path(host(2), 2): html(GONE_PAGE)})
    assert tick(factory, web) == 3
    first, second, third = jobs(factory, [gone, said, still])
    assert (first.status, second.status, third.status) == (
        JobStatus.closed,
        JobStatus.closed,
        JobStatus.found,
    )
    assert first.liveness is not None
    assert first.liveness["reason"] == "http_404"
    assert first.liveness["moved"] is True
    assert status_events(factory, gone) == [
        {"from": "found", "to": "closed", "via": "liveness", "check": "http_404"}
    ]
    assert third.liveness is not None
    assert third.liveness["verdict"] == "no_closure"
    assert status_events(factory, still) == []


@pytest.mark.parametrize(
    "page",
    [
        Page(503, b"busy"),
        Page(403, b"forbidden"),
        Page(429, b"slow down"),
        html("<html><body><p>Just a moment...</p></body></html>"),
    ],
)
def test_an_unreachable_page_is_never_a_closed_one(
    factory: sessionmaker[Session], ann: uuid.UUID, page: Page
) -> None:
    (posting,) = add(factory, ann, [{"n": 1}])
    assert tick(factory, Web({path(host(1), 1): page})) == 1
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    assert job.liveness is not None
    assert job.liveness["verdict"] == "unconfirmed"


def test_a_posting_the_person_reopened_is_only_proposed(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1, "liveness": checked(NOW - timedelta(days=8))}])
    with factory() as db:
        job = jobs_service.lock_job(db, ann, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.closed, NOW, via="liveness")
        jobs_service.set_status(db, job, JobStatus.found, NOW)
        db.commit()
    assert tick(factory, Web({path(host(1), 1): Page(410, b"gone")})) == 1
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    assert job.liveness is not None
    assert (job.liveness["verdict"], job.liveness["moved"]) == (CLOSED, False)


# --- the bounds ----------------------------------------------------------------------------------


def test_the_bounds_hold_over_a_day_across_restarts(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    add(factory, ann, [{"n": n} for n in range(1, 6)])
    web = Web()
    # Each tick is a fresh worker: the count is read from the database.
    assert tick(factory, web, NOW, per_day=3, per_tick=2) == 2
    assert tick(factory, web, NOW + timedelta(minutes=1), per_day=3, per_tick=2) == 1
    assert tick(factory, web, NOW + timedelta(minutes=2), per_day=3, per_tick=2) == 0
    assert tick(factory, web, NOW + timedelta(hours=23), per_day=3, per_tick=2) == 0
    assert len(web.seen) == 3
    # The first two checks leave the 24 hours: two more.
    later = NOW + timedelta(hours=24, seconds=30)
    assert tick(factory, web, later, per_day=3, per_tick=2) == 2
    assert len(web.seen) == 5


def test_a_click_s_check_takes_from_the_day_s_room(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    add(factory, ann, [{"n": n, "liveness": checked(NOW - timedelta(hours=2))} for n in (1, 2)])
    add(factory, ann, [{"n": n} for n in (3, 4, 5)])
    web = Web()
    assert tick(factory, web, per_day=3) == 1


def test_one_tick_at_a_time_across_two_workers(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    add(factory, ann, [{"n": n} for n in range(1, 5)])
    seen: list[str] = []

    async def run() -> tuple[int, int]:
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["host"])
            entered.set()
            await release.wait()
            page = html(OPEN_PAGE)
            return httpx.Response(200, headers=page.headers, stream=httpx.ByteStream(page.body))

        first = asyncio.ensure_future(liveness_timer.tick(ctx(factory, slow, NOW, per_tick=2)))
        await entered.wait()
        second = await liveness_timer.tick(ctx(factory, slow, NOW, per_tick=2))
        release.set()
        return await first, second

    assert asyncio.run(run()) == (2, 0)
    assert len(seen) == 2


def test_the_persons_take_turns_within_a_tick(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    add(factory, ann, [{"n": n, "published_on": date(2026, 7 + n, 1)} for n in (1, 2, 3)])
    add(factory, ben, [{"n": n, "published_on": date(2026, n - 3, 1)} for n in (11, 12, 13)])
    web = Web()
    assert tick(factory, web, per_tick=4) == 4
    firsts = sorted([ann, ben])
    order = {ann: (1, 2), ben: (11, 12)}
    expected = [order[firsts[0]][0], order[firsts[1]][0], order[firsts[0]][1], order[firsts[1]][1]]
    assert web.seen == [path(host(n), n) for n in expected]


def test_one_posting_of_a_host_a_tick(factory: sessionmaker[Session], ann: uuid.UUID) -> None:
    add(factory, ann, [{"n": n, "host": "careers.example-robotics.example"} for n in (1, 2, 3)])
    add(factory, ann, [{"n": 4, "host": "www.careers.example-robotics.example"}])
    web = Web()
    assert tick(factory, web) == 1
    assert tick(factory, web, NOW + timedelta(minutes=1)) == 1
    assert len(web.seen) == 2


def test_a_host_that_turned_a_check_away_rests_a_day_for_every_posting_of_the_person(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": 1, "host": site}])
    later = add(factory, ann, [{"n": 2, "host": site, "published_on": date(2026, 11, 20)}])
    web = Web({path(site, 1): Page(429, b"slow down")})
    assert tick(factory, web) == 1
    assert web.seen == [path(site, 1)]
    add(factory, ben, [{"n": 3, "host": site}])
    # Ann's other posting of that host waits; Ben's is his own.
    assert tick(factory, web, NOW + timedelta(minutes=1)) == 1
    assert web.seen[-1] == path(site, 3)
    assert tick(factory, web, NOW + timedelta(hours=23, minutes=59)) == 0
    assert tick(factory, web, NOW + timedelta(hours=24, minutes=1)) == 1
    assert web.seen[-1] == path(site, 2)
    assert jobs(factory, later)[0].liveness is not None


@pytest.mark.parametrize("reason", sorted(liveness_timer.REST_REASONS))
def test_every_wall_rests_its_host(
    factory: sessionmaker[Session], ann: uuid.UUID, reason: str
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": 1, "host": site, "liveness": checked(NOW, reason, "unconfirmed")}])
    add(factory, ann, [{"n": 2, "host": site}])
    web = Web()
    assert tick(factory, web) == 0
    assert web.seen == []


def test_a_host_a_running_search_reads_is_left_alone(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": 1, "host": site}, {"n": 2}])
    with factory() as db, db.begin():
        db.add(
            Sweep(
                user_id=ben,
                trigger=SweepTrigger.manual,
                status=SweepStatus.running,
                stats={"sources": [{"source_id": "s1", "host": f"www.{site}"}]},
            )
        )
    web = Web()
    assert tick(factory, web) == 1
    assert web.seen == [path(host(2), 2)]


def test_a_person_with_no_open_posting_costs_no_query_of_her_own(
    factory: sessionmaker[Session], engine: sa.Engine, ann: uuid.UUID
) -> None:
    add(factory, ann, [{"n": 1, "status": JobStatus.applied}, {"n": 2, "status": JobStatus.closed}])
    statements: list[str] = []

    def count(*args: Any) -> None:
        statements.append(args[2])

    sa.event.listen(engine, "before_cursor_execute", count)
    try:
        with factory() as db:
            assert liveness_timer.plan(db, NOW, Bounds()) == []
    finally:
        sa.event.remove(engine, "before_cursor_execute", count)
    # Who has an open posting, and nothing more.
    assert len(statements) == 1


def test_the_bounds_are_settings_with_the_planner_defaults() -> None:
    defaults = SimpleNamespace(
        **{name: field.default for name, field in Settings.model_fields.items()}
    )
    assert Bounds.of(defaults) == Bounds(per_day=150, per_tick=10, rest_hours=24, again_days=7)


def test_zero_a_day_asks_nothing(factory: sessionmaker[Session], ann: uuid.UUID) -> None:
    add(factory, ann, [{"n": 1}])
    web = Web()
    assert tick(factory, web, per_day=0) == 0
    assert web.seen == []


def test_the_worker_runs_the_tick_every_minute() -> None:
    from app import worker

    spec = worker.sweeps_queue()
    (found,) = [c for c in spec.cron_jobs if c.name == "cron:liveness_timer"]
    assert found.coroutine is liveness_timer.tick
    assert found.timeout_s == liveness_timer.TICK_TIMEOUT_S
    assert "liveness_timer" in {f.name for f in spec.functions}


# --- a closing date that passed ------------------------------------------------------------------


def test_a_closing_date_closes_the_posting_once_it_passed_everywhere(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    found, shortlisted, ready, coming = add(
        factory,
        ann,
        [
            {"n": 1, "closes_on": date(2026, 11, 20)},
            {"n": 2, "closes_on": date(2026, 11, 20), "status": JobStatus.shortlisted},
            {"n": 3, "closes_on": date(2026, 11, 20), "status": JobStatus.cv_ready},
            {"n": 4, "closes_on": date(2026, 11, 21)},
        ],
    )
    web = Web()
    # 21 November at 11:59 UTC is still 20 November at UTC-12.
    before = datetime(2026, 11, 21, 11, 59, tzinfo=UTC)
    asyncio.run(liveness_timer.tick(ctx(factory, web, before, per_day=0)))
    assert [job.status for job in jobs(factory, [found, shortlisted])] == [
        JobStatus.found,
        JobStatus.shortlisted,
    ]
    at = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    asyncio.run(liveness_timer.tick(ctx(factory, web, at, per_day=0)))
    first, second, third, fourth = jobs(factory, [found, shortlisted, ready, coming])
    assert (first.status, second.status) == (JobStatus.closed, JobStatus.closed)
    assert (third.status, fourth.status) == (JobStatus.cv_ready, JobStatus.found)
    assert first.liveness is not None
    assert (first.liveness["reason"], first.liveness["moved"]) == ("closing_date", True)
    assert status_events(factory, found) == [
        {"from": "found", "to": "closed", "via": "liveness", "check": "closing_date"}
    ]
    assert liveness_check.closed_sentence(first.liveness) == (
        "This posting is closed: its closing date passed."
    )
    assert web.seen == []


def test_a_closing_date_spends_no_check_of_the_day(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    add(factory, ann, [{"n": n, "closes_on": date(2026, 11, 1)} for n in (1, 2, 3)])
    add(factory, ann, [{"n": 4}])
    web = Web()
    assert tick(factory, web, per_day=1) == 1
    assert web.seen == [path(host(4), 4)]


def test_a_reopened_posting_whose_date_passed_is_not_closed_again(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1, "closes_on": date(2026, 11, 20)}])
    tick(factory, Web(), per_day=0)
    with factory() as db:
        job = jobs_service.lock_job(db, ann, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.found, NOW)
        db.commit()
    # The person's Check now after the date: its finding stays, no proposal comes back.
    with factory() as db:
        liveness_check.record(db, ann, posting, Finding("no_closure", "none"), NOW, job.url)
    later = NOW + timedelta(days=1)
    web = Web()
    tick(factory, web, later)
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    assert job.liveness is not None
    assert job.liveness["reason"] == "none"
    assert web.seen == []
    events = status_events(factory, posting)
    assert [e["to"] for e in events] == ["closed", "found"]


def test_a_reopened_posting_never_checked_since_its_date_is_proposed(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(
        factory,
        ann,
        [
            {
                "n": 1,
                "closes_on": date(2026, 11, 20),
                "liveness": checked(datetime(2026, 11, 2, tzinfo=UTC)),
            }
        ],
    )
    with factory() as db:
        job = jobs_service.lock_job(db, ann, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.closed, NOW, via="liveness")
        jobs_service.set_status(db, job, JobStatus.found, NOW)
        db.commit()
    tick(factory, Web(), per_day=0)
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    assert job.liveness is not None
    assert (job.liveness["reason"], job.liveness["moved"]) == ("closing_date", False)
    stored = job.liveness["checked_at"]
    tick(factory, Web(), NOW + timedelta(minutes=1), per_day=0)
    assert jobs(factory, [posting])[0].liveness["checked_at"] == stored  # type: ignore[index]
