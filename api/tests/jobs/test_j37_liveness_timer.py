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
from tests.jobs.test_liveness_check import COPY, HINT_PAGE, Page, html, instant, resolver

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


def test_never_checked_first_published_longest_ago_then_the_oldest_check(
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
                "published_on": date(2026, 5, 1),
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
    assert asyncio.run(liveness_timer._check(checker, factory, pick, lambda: NOW, {})) is False
    assert web.seen == []


def acted(factory: sessionmaker[Session], user: uuid.UUID, posting: uuid.UUID) -> None:
    """The person's click (Write), in a session of its own."""
    with factory() as db:
        job = jobs_service.lock_job(db, user, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.cv_ready, NOW)
        db.commit()


def test_a_posting_the_person_acts_on_during_its_request_is_not_closed(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1}])

    def gone_after_the_click(request: httpx.Request) -> httpx.Response:
        acted(factory, ann, posting)
        return httpx.Response(410, stream=httpx.ByteStream(b"gone"))

    assert tick(factory, gone_after_the_click) == 1
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.cv_ready
    assert job.liveness is not None
    assert (job.liveness["verdict"], job.liveness["moved"]) == (CLOSED, False)
    assert [event["to"] for event in status_events(factory, posting)] == ["cv_ready"]


def test_a_closing_date_leaves_a_posting_the_person_acted_on_after_it_was_read(
    factory: sessionmaker[Session], ann: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    (posting,) = add(factory, ann, [{"n": 1, "closes_on": date(2026, 11, 20)}])
    real = liveness_timer._propose_one

    def click_then_propose(*args: Any) -> int:
        # Between the pass's read of the open postings and its row lock.
        acted(factory, ann, posting)
        return real(*args)

    monkeypatch.setattr(liveness_timer, "_propose_one", click_then_propose)
    assert liveness_timer.propose_passed(factory, NOW) == 0
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.cv_ready
    assert job.liveness is None
    assert [event["to"] for event in status_events(factory, posting)] == ["cv_ready"]


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


# --- a finding that cannot be stored -------------------------------------------------------------


def test_a_page_s_words_the_database_refuses_are_dropped_before_the_store(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1}])
    page = f"<html><body><p>{COPY}</p><h2>This job is no longer\x00 available.</h2></body></html>"
    web = Web({path(host(1), 1): html(page)})
    for minute in range(5):
        tick(factory, web, NOW + timedelta(minutes=minute), per_day=2)
    assert web.seen == [path(host(1), 1)]
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.closed
    assert job.liveness is not None
    assert (job.liveness["reason"], job.liveness["moved"]) == ("page_says", True)
    assert job.liveness["sentence"] == "This job is no longer available."


@pytest.mark.parametrize(
    ("words", "stored"),
    [
        ("No longer\x00 open.", "No longer open."),
        ("Closed\ud800 now\udfff.", "Closed now."),
        ("\x00", None),
        ("", None),
        (None, None),
        ("Fermée — \U0001f6ab", "Fermée — \U0001f6ab"),
    ],
)
def test_what_jsonb_refuses_is_dropped(words: str | None, stored: str | None) -> None:
    assert liveness_check.storable(words) == stored


@pytest.mark.parametrize("refused", [{"http_410"}, {"http_410", "check_failed"}])
def test_a_check_whose_finding_cannot_be_stored_counts_and_waits_as_any_check(
    factory: sessionmaker[Session],
    ann: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    refused: set[str],
) -> None:
    (posting,) = add(factory, ann, [{"n": 1, "published_on": date(2026, 1, 1)}])
    real = liveness_check.record

    def record(
        db: Session, user: uuid.UUID, job_id: uuid.UUID, finding: Finding, *rest: Any, **named: Any
    ) -> Any:
        if finding.reason in refused:
            raise RuntimeError("the database refused it")
        return real(db, user, job_id, finding, *rest, **named)

    monkeypatch.setattr(liveness_check, "record", record)
    web = Web({path(host(1), 1): Page(410, b"gone")})
    clock = [NOW]
    # One worker over the minutes: its memory keeps what no store took.
    worker = ctx(factory, web, NOW, per_day=2)
    worker["clock"] = lambda: clock[0]

    async def ticks(start: datetime) -> list[int]:
        asked = []
        for minute in range(5):
            clock[0] = start + timedelta(minutes=minute)
            asked.append(await liveness_timer.tick(worker))
        return asked

    assert asyncio.run(ticks(NOW)) == [1, 0, 0, 0, 0]
    assert web.seen == [path(host(1), 1)]
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    if "check_failed" in refused:
        assert job.liveness is None
    else:
        assert job.liveness is not None
        assert (job.liveness["verdict"], job.liveness["reason"]) == ("unconfirmed", "check_failed")
    # It took one of the day's two: one more posting is asked, then none.
    add(factory, ann, [{"n": 2}, {"n": 3}])
    assert asyncio.run(ticks(NOW + timedelta(minutes=10))) == [1, 0, 0, 0, 0]
    assert len(web.seen) == 2
    # After `again_days`, the page is asked again.
    worker["settings"].jobs_liveness_checks_per_day = 150
    assert asyncio.run(ticks(NOW + timedelta(days=7, minutes=1)))[0] >= 1
    assert web.seen.count(path(host(1), 1)) == 2


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
    # Findings stored without a request leave the room as it was; a closing date's proposal
    # whose page the timer asked takes from it.
    an_hour_ago = NOW - timedelta(hours=1)
    stored = [checked(an_hour_ago, r, "unconfirmed") for r in ("linkedin", "no_address")]
    stored.append(checked(an_hour_ago, "closing_date", "may_be_closed"))
    stored.append({**checked(an_hour_ago, "closing_date", "may_be_closed"), "page": "none"})
    add(
        factory,
        ann,
        [
            {"n": 20 + i, "status": JobStatus.closed, "liveness": finding}
            for i, finding in enumerate(stored)
        ],
    )
    web = Web()
    assert tick(factory, web, per_day=4) == 1


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


def test_a_host_that_turned_a_check_away_rests_a_day_for_every_person(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": 1, "host": site}])
    later = add(factory, ann, [{"n": 2, "host": site, "published_on": date(2026, 11, 20)}])
    web = Web({path(site, 1): Page(429, b"slow down")})
    assert tick(factory, web) == 1
    assert web.seen == [path(site, 1)]
    later += add(factory, ben, [{"n": 3, "host": site}, {"n": 4, "host": f"www.{site}"}])
    # The host turned the machine's one address away: Ann's other posting and Ben's wait.
    assert tick(factory, web, NOW + timedelta(minutes=1)) == 0
    assert tick(factory, web, NOW + timedelta(hours=23, minutes=59)) == 0
    assert web.seen == [path(site, 1)]
    for minute in (1, 2, 3):
        assert tick(factory, web, NOW + timedelta(hours=24, minutes=minute)) == 1
    assert sorted(web.seen[1:]) == [path(site, 2), path(site, 3), path(f"www.{site}", 4)]
    assert all(job.liveness is not None for job in jobs(factory, later))


@pytest.mark.parametrize("reason", sorted(liveness_timer.REST_REASONS))
def test_every_wall_rests_its_host(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID, reason: str
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": 1, "host": site, "liveness": checked(NOW, reason, "unconfirmed")}])
    add(factory, ann, [{"n": 2, "host": site}])
    add(factory, ben, [{"n": 3, "host": site}])
    web = Web()
    assert tick(factory, web) == 0
    assert web.seen == []


def test_a_host_down_twice_in_a_row_is_asked_once_an_hour(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    site = "careers.example-robotics.example"
    add(factory, ann, [{"n": n, "host": site, "published_on": date(2026, n, 1)} for n in (1, 2, 3)])
    add(factory, ben, [{"n": 4, "host": f"www.{site}", "published_on": date(2026, 4, 1)}])
    web = Web({path(site, n): Page(503, b"busy") for n in (1, 2, 3)})
    web.pages[path(f"www.{site}", 4)] = Page(500, b"error")
    assert tick(factory, web) == 1
    assert tick(factory, web, NOW + timedelta(minutes=1)) == 1
    # Down twice in a row: nobody's posting of the host is asked for an hour.
    assert tick(factory, web, NOW + timedelta(minutes=2)) == 0
    assert tick(factory, web, NOW + timedelta(minutes=60)) == 0
    assert tick(factory, web, NOW + timedelta(minutes=61, seconds=1)) == 1
    assert tick(factory, web, NOW + timedelta(minutes=62)) == 0
    assert len(web.seen) == 3


@pytest.mark.parametrize(
    ("latest", "before", "ago", "asked"),
    [
        ("server_error", "server_error", timedelta(minutes=59), 0),
        ("timeout", "network", timedelta(minutes=59), 0),
        ("network", "timeout", timedelta(minutes=10), 0),
        # Once, or after a good answer, or past the hour: asked.
        ("server_error", "none", timedelta(minutes=10), 1),
        ("none", "server_error", timedelta(minutes=10), 1),
        ("server_error", "server_error", timedelta(minutes=61), 1),
        ("unexpected_answer", "server_error", timedelta(minutes=10), 1),
        ("server_error", "unreadable", timedelta(minutes=10), 1),
    ],
)
def test_two_down_answers_in_a_row_rest_the_host(
    factory: sessionmaker[Session],
    ann: uuid.UUID,
    latest: str,
    before: str,
    ago: timedelta,
    asked: int,
) -> None:
    site = "careers.example-robotics.example"
    stored = [(latest, NOW - ago), (before, NOW - ago - timedelta(minutes=1))]
    add(
        factory,
        ann,
        [
            {"n": 1 + i, "host": site, "liveness": checked(at, reason, "unconfirmed")}
            for i, (reason, at) in enumerate(stored)
        ],
    )
    add(factory, ann, [{"n": 3, "host": site}])
    assert tick(factory, Web()) == asked


@pytest.mark.parametrize(
    ("kind", "site"),
    [
        ("indeed", "fr.indeed.example"),
        ("welcome_to_the_jungle", "www.welcometothejungle.example"),
        ("freework", "www.free-work.example"),
    ],
)
def test_a_platform_her_search_met_a_checkpoint_on_rests_for_her_during_its_pause(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID, kind: str, site: str
) -> None:
    add(factory, ann, [{"n": 1, "host": site}])
    add(factory, ben, [{"n": 2, "host": site}])
    met = NOW - timedelta(days=6, hours=23)
    with factory() as db, db.begin():
        db.add(
            Sweep(
                user_id=ann,
                trigger=SweepTrigger.manual,
                status=SweepStatus.done,
                finished_at=met,
                stats={"sources": [{"kind": kind, "checkpoint_at": met.isoformat()}]},
            )
        )
    web = Web()
    # Her pause is hers, as the search keeps it: Ben's posting of the platform is asked.
    assert tick(factory, web) == 1
    assert web.seen == [path(site, 2)]
    assert tick(factory, web, NOW + timedelta(minutes=59, seconds=59)) == 0
    assert tick(factory, web, NOW + timedelta(hours=1)) == 1
    assert web.seen == [path(site, 2), path(site, 1)]


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
    assert (found.minute, found.second) == (None, liveness_timer.TICK_SECOND)
    assert found.timeout_s == liveness_timer.TICK_TIMEOUT_S
    assert "liveness_timer" in {f.name for f in spec.functions}


# --- a closing date that passed ------------------------------------------------------------------


def closing(
    day: date, quote: str | None = "Applications close on 20 November 2026."
) -> dict[str, Any]:
    """A posting's stated closing date, with the signal its sentence was read from."""
    signals = [{"code": "closing", "quote": quote, "value": day.isoformat()}] if quote else None
    return {"closes_on": day, "signals": signals}


def proposal(at: datetime, sentence: str | None, **page: str) -> dict[str, Any]:
    return {
        "verdict": "may_be_closed",
        "reason": "closing_date",
        "sentence": sentence,
        "checked_at": at.isoformat(),
        "moved": False,
        **page,
    }


def test_a_closing_date_that_passed_everywhere_only_proposes_the_posting(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    found, shortlisted, ready, coming = add(
        factory,
        ann,
        [
            {"n": 1, **closing(date(2026, 11, 20))},
            {"n": 2, **closing(date(2026, 11, 20), None), "status": JobStatus.shortlisted},
            {"n": 3, **closing(date(2026, 11, 20)), "status": JobStatus.cv_ready},
            {"n": 4, **closing(date(2026, 11, 21))},
        ],
    )
    web = Web()
    # 21 November at 11:59 UTC is still 20 November at UTC-12.
    before = datetime(2026, 11, 21, 11, 59, tzinfo=UTC)
    tick(factory, web, before, per_day=0)
    assert [job.liveness for job in jobs(factory, [found, shortlisted])] == [None, None]
    at = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    tick(factory, web, at, per_day=0)
    first, second, third, fourth = jobs(factory, [found, shortlisted, ready, coming])
    assert [job.status for job in (first, second, third, fourth)] == [
        JobStatus.found,
        JobStatus.shortlisted,
        JobStatus.cv_ready,
        JobStatus.found,
    ]
    assert first.liveness == proposal(at, "Applications close on 20 November 2026.")
    assert second.liveness == proposal(at, None)
    assert (third.liveness, fourth.liveness) == (None, None)
    assert [status_events(factory, job) for job in (found, shortlisted)] == [[], []]
    assert web.seen == []
    # Proposed once: the next tick stores nothing new.
    tick(factory, web, at + timedelta(minutes=1), per_day=0)
    assert jobs(factory, [found])[0].liveness == proposal(
        at, "Applications close on 20 November 2026."
    )


def test_a_closing_date_s_proposal_spends_nothing_and_its_page_is_asked_first(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    dated = add(factory, ann, [{"n": n, **closing(date(2026, 11, 1))} for n in (1, 2, 3)])
    # Checked before its date passed, two days ago: asked again first all the same.
    dated += add(
        factory,
        ann,
        [{"n": 5, **closing(date(2026, 11, 26)), "liveness": checked(NOW - timedelta(days=2))}],
    )
    add(factory, ann, [{"n": 4, "published_on": date(2026, 1, 1)}])
    web = Web()
    assert tick(factory, web, per_day=4, per_tick=4) == 4
    assert sorted(web.seen) == sorted(path(host(n), n) for n in (1, 2, 3, 5))
    assert all(job.liveness and job.liveness["page"] == "none" for job in jobs(factory, dated))


def test_its_own_page_says_whether_a_posting_whose_date_passed_is_closed(
    factory: sessionmaker[Session], ann: uuid.UUID, ben: uuid.UUID
) -> None:
    gone, still, walled, hinted = add(
        factory, ann, [{"n": n, **closing(date(2026, 11, 20))} for n in (1, 2, 3, 4)]
    )
    web = Web(
        {
            path(host(1), 1): Page(410, b"gone"),
            path(host(3), 3): Page(429, b"slow down"),
            path(host(4), 4): html(HINT_PAGE),
        }
    )
    assert tick(factory, web) == 4
    first, second, third, fourth = jobs(factory, [gone, still, walled, hinted])
    assert first.status is JobStatus.closed
    assert first.liveness is not None
    assert (first.liveness["reason"], first.liveness["moved"]) == ("http_410", True)
    assert status_events(factory, gone) == [
        {"from": "found", "to": "closed", "via": "liveness", "check": "http_410"}
    ]
    sentence = "Applications close on 20 November 2026."
    assert (second.status, third.status, fourth.status) == (JobStatus.found,) * 3
    assert second.liveness == proposal(NOW, sentence, page="none")
    assert third.liveness == proposal(NOW, sentence, page="rate_limited")
    # A page that proposes gives its own words.
    assert fourth.liveness is not None
    assert (fourth.liveness["reason"], fourth.liveness["sentence"]) == ("page_hints", "Job expired")
    # The wall behind a proposal kept rests its host for everyone.
    add(factory, ben, [{"n": 3, "host": host(3)}])
    assert tick(factory, web, NOW + timedelta(minutes=1)) == 0


def test_a_closing_date_s_proposal_stands_until_a_page_or_the_person_says_otherwise(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    (posting,) = add(factory, ann, [{"n": 1, **closing(date(2026, 11, 20))}])
    sentence = "Applications close on 20 November 2026."
    web = Web()
    assert tick(factory, web) == 1
    # Asked again after `again_days`, the page still shows no sign of closing: still proposed.
    assert tick(factory, web, NOW + timedelta(days=6, hours=23)) == 0
    later = NOW + timedelta(days=7, minutes=1)
    assert tick(factory, web, later) == 1
    assert jobs(factory, [posting])[0].liveness == proposal(later, sentence, page="none")
    # The person's Check now gives the page's own answer, and the date proposes no more.
    with factory() as db:
        liveness_check.record(
            db, ann, posting, Finding("no_closure", "none"), later, f"https://{host(1)}/careers/1"
        )
    tick(factory, web, later + timedelta(minutes=1), per_day=0)
    (job,) = jobs(factory, [posting])
    assert job.status is JobStatus.found
    assert job.liveness is not None
    assert (job.liveness["verdict"], job.liveness["reason"]) == ("no_closure", "none")
    assert len(web.seen) == 2


def test_a_closing_date_s_proposal_is_not_a_fresh_check_for_a_click(
    factory: sessionmaker[Session], ann: uuid.UUID
) -> None:
    assert not liveness_check.fresh(proposal(NOW, None), NOW)
    assert liveness_check.fresh(proposal(NOW, None, page="none"), NOW)
    assert not liveness_check.fresh(proposal(NOW - timedelta(hours=2), None, page="none"), NOW)
