"""The sources of a sweep that make no browser call run side by side (owner, 2026-10-31: "Run
sources in parallel"; app/jobs/sweep.py, `run_sweep` and `Commits`), against real Postgres: they
overlap (controlled events, never a wall clock measure), the websites start after them with the
time left, two of them finding one posting at once store one row, every source's stats entry is
its own, one that fails ends alone, a cancellation or the time limit ends them all, and the
worker gives LinkedIn's public job search to the side by side step."""

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.browser import finder, sources
from app.jobs import feeds, sweep
from app.jobs.boards import run as boards
from app.jobs.feeds import keys
from app.jobs.models import Job, SweepStatus
from tests.browser.test_linkedin_guest import Allow, LinkedInFake, card
from tests.browser.test_linkedin_guest import section as linkedin_section
from tests.jobs.sweep_helpers import (
    HOST,
    SITE,
    jobs_of,
    new_sweep,
    read_sweep,
    search,
    set_profile,
    website_runner,
    websites,
)
from tests.jobs.test_boards_sweep import ROBOTICS, fixture_answers
from tests.jobs.test_boards_sweep import hook as boards_hook
from tests.jobs.test_job_feeds_sweep import KEYRING, fake_api, feeds_search

LABS = {"company": "Example Labs", "ats": "greenhouse", "board": "examplelabs"}
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
Hook = Callable[..., Awaitable[None]]
# A guard, never a measure: sources run one after the other would wait at their barrier
# forever; they fail after this instead.
TOGETHER_S = 30.0


async def meet(together: asyncio.Barrier) -> None:
    async with asyncio.timeout(TOGETHER_S):
        await together.wait()


def now() -> datetime:
    return NOW


def profile(factory: sessionmaker[Session], user: uuid.UUID, **overrides: Any) -> uuid.UUID:
    """A person with LinkedIn's alert e-mails, one job feed, one company board and one website
    ticked, and a running sweep of them."""
    ticked: dict[str, Any] = {
        "alert_emails": {"linkedin": True},
        "job_feeds": ["arbeitnow"],
        "company_boards": [LABS],
        "interactive_sites": [SITE],
    }
    set_profile(factory, user, search(**(ticked | overrides)))
    return new_sweep(factory, user)


def entry_of(stats: Any, kind: str) -> dict[str, Any]:
    sources_ = stats.sources if isinstance(stats, sweep.Stats) else stats["sources"]
    return next(entry for entry in sources_ if entry["kind"] == kind)


def found(title: str, url: str, company: str = "Example Robotics", place: str = "Paris, France"):
    return finder.VerifiedFinding(
        title=title,
        location=place,
        url=url,
        text=f"{title}. We build agents with Python and LLMs.",
        company=company,
        published_on=None,
        evidence={"title": title},
        detail_read_at=NOW.isoformat(),
    )


def source(
    kind: str,
    log: list[str],
    *,
    together: asyncio.Barrier | None = None,
    stores: tuple[finder.VerifiedFinding, ...] = (),
    label: str | None = None,
    then: Callable[[], Awaitable[None]] | None = None,
    error: str | None = None,
    raises: bool = False,
) -> Hook:
    """A fake source that makes no request: it binds its frozen entry, waits at `together` until
    every source of the barrier runs (so the sources can only finish when they overlap), stores
    `stores` in one batch, runs `then`, and ends with `error` (or raises)."""

    async def hook(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float) -> None:
        entry = entry_of(stats, kind)
        entry.update(phase="running", outcome=None, reason=None, found=0, postings=0, error=None)
        log.append(f"{kind} started")
        assert plan.commits is not None
        plan.commits.admit(entry["source_id"])
        await progress()
        if together is not None:
            await meet(together)
        if stores:
            await plan.commits.save(
                entry["source_id"], label or entry["host"], finder.VerifiedBatch(stores)
            )
        if then is not None:
            await then()
        await plan.commits.settle(entry["source_id"])
        if raises:
            log.append(f"{kind} raised")
            raise RuntimeError("a bug in one source")
        entry["error"] = error
        sweep.finish_source(entry)
        await progress()
        log.append(f"{kind} ended")

    return hook


class FakeTime:
    """The sweep's monotonic clock (sweep.time), moved by the fake sources."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def spend(self, until: float) -> Callable[[], Awaitable[None]]:
        async def then() -> None:
            # A source started at 0 ends `until` seconds in: side by side, the sweep has
            # taken as long as the slowest of them.
            self.now = max(self.now, until)

        return then


# --- side by side, then the websites ------------------------------------------------------------


def test_the_api_sources_run_side_by_side_then_the_websites_with_the_time_left(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep_id = profile(factory, alice)
    clock = FakeTime()
    monkeypatch.setattr(sweep, "time", SimpleNamespace(monotonic=clock.monotonic))
    log: list[str] = []
    together = asyncio.Barrier(3)  # each source waits until the three run
    seen: dict[str, Any] = {}

    async def the_websites(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        log.append("websites started")
        seen["left"] = deadline - clock.now
        seen["parsing"] = plan.parsing
        site = source("custom", log, stores=(found("GenAI Engineer", f"https://{HOST}/jobs/1"),))
        await site(plan, stats, progress, deadline)

    def run() -> None:
        asyncio.run(
            sweep.run_sweep(
                factory,
                sweep_id,
                now,
                alerts=source("alerts", log, together=together, then=clock.spend(120)),
                feeds=source("arbeitnow", log, together=together, then=clock.spend(180)),
                apis=(source("board", log, together=together, then=clock.spend(300)), None),
                browser=the_websites,
            )
        )

    run()
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.done, None)
    # The three started before any ended: had they run one after the other, the first would
    # have waited at the barrier forever.
    assert sorted(log[:3]) == ["alerts started", "arbeitnow started", "board started"]
    assert sorted(log[3:6]) == ["alerts ended", "arbeitnow ended", "board ended"]
    assert log[6:] == ["websites started", "custom started", "custom ended"]
    # The websites get what the slowest source left (900 s less 300), not 900 s less the sum.
    assert seen["left"] == sweep.SWEEP_TIMEOUT_S - 300
    assert row.stats["duration"] == 300
    assert isinstance(seen["parsing"], asyncio.Semaphore)
    assert {(s["kind"], s["phase"], s["outcome"]) for s in row.stats["sources"]} == {
        ("alerts", "done", "no_matches"),
        ("arbeitnow", "done", "no_matches"),
        ("board", "done", "no_matches"),
        ("custom", "done", "complete"),
    }


def test_two_sources_finding_one_posting_at_the_same_moment_store_one_row(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    sweep_id = profile(factory, alice, interactive_sites=[])
    log: list[str] = []
    together = asyncio.Barrier(3)
    view = "https://www.linkedin.com/jobs/view/4100000031/"
    run = sweep.run_sweep(
        factory,
        sweep_id,
        now,
        # LinkedIn's alert e-mails and another source share LinkedIn's address; a job feed and
        # a company board name the same employer, title and place at two addresses.
        alerts=source(
            "alerts", log, together=together, label="mail:linkedin",
            stores=(found("GenAI Engineer", view),),
        ),
        feeds=source(
            "arbeitnow", log, together=together, label="arbeitnow",
            stores=(
                found("Forward Deployed Engineer", "https://www.arbeitnow.com/jobs/fde-1"),
                found("GenAI Engineer", view),
            ),
        ),
        apis=(
            source(
                "board", log, together=together, label="greenhouse:examplelabs",
                stores=(
                    found("Forward Deployed Engineer", "https://boards.greenhouse.io/x/jobs/7"),
                ),
            ),
        ),
    )  # fmt: skip
    asyncio.run(run)
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    jobs = jobs_of(factory, alice)
    assert sorted(job.title for job in jobs) == ["Forward Deployed Engineer", "GenAI Engineer"]
    # One row each, naming every source that found it (browser B10), the first one first.
    names = {job.title: set(sweep.noted_sources(job.source)) for job in jobs}
    assert names["GenAI Engineer"] == {"mail:linkedin", "arbeitnow"}
    assert names["Forward Deployed Engineer"] == {"arbeitnow", "greenhouse:examplelabs"}
    stats = row.stats
    assert (stats["seen"], stats["new"], stats["duplicates"]) == (4, 2, 2)
    # Each source's entry counts what it found; the rows and the duplicates add up.
    entries = [entry_of(stats, kind) for kind in ("alerts", "arbeitnow", "board")]
    assert [entry["found"] for entry in entries] == [1, 2, 1]
    assert sum(entry["postings"] for entry in entries) == len(jobs)
    assert sum(entry.get("duplicates", 0) for entry in entries) == 2
    for entry in entries:
        assert entry["found"] == entry["postings"] + entry.get("duplicates", 0)


def test_linkedin_alerts_beside_the_public_job_search_store_one_row(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """The real source of LinkedIn's public job search, run by the worker beside the alert
    e-mails: an alert stores a posting while the search is reading its page."""
    data = linkedin_section().model_dump(mode="json")
    set_profile(factory, alice, {**data, "alert_emails": {"linkedin": True}})
    sweep_id = new_sweep(factory, alice)
    clock = [0.0]
    page = "".join(
        [
            card("4100000021", "GenAI Engineer"),
            card("4100000022", "GenAI Engineer", company="Example Labs", place="Lyon, France"),
        ]
    )
    fake = LinkedInFake(clock, pages={0: page})
    reading = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            reading.set()  # the public job search has its page: the alert stores now
        return fake.handler(request)

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    runner = sources.Runner(
        None,  # type: ignore[arg-type]  no lease is ever asked for
        lambda *_: None,
        sleep=sleep,
        monotonic=lambda: clock[0],
        pacer=sources.MemoryPacer(lambda: clock[0]),
        interactive_policy=Allow(),
        guest_http=lambda: httpx.MockTransport(handler),
    )
    log: list[str] = []

    async def wait_for_the_page() -> None:
        await reading.wait()

    alert = found(
        "GenAI Engineer", "https://www.linkedin.com/jobs/view/4100000021/", place="Paris, France"
    )

    async def alerts(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        await wait_for_the_page()
        await source("alerts", log, label="mail:linkedin", stores=(alert,))(
            plan, stats, progress, deadline
        )

    linkedin, the_websites = worker.split_websites(runner)
    run = sweep.run_sweep(
        factory, sweep_id, now, timeout_s=60, alerts=alerts, apis=(linkedin,), browser=the_websites
    )
    asyncio.run(run)
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    jobs = {job.url: job for job in jobs_of(factory, alice)}
    assert sorted(jobs) == [
        "https://www.linkedin.com/jobs/view/4100000021/",
        "https://www.linkedin.com/jobs/view/4100000022/",
    ]
    both = set(sweep.noted_sources(jobs["https://www.linkedin.com/jobs/view/4100000021/"].source))
    assert both == {"mail:linkedin", "www.linkedin.com"}
    guest, mail = entry_of(row.stats, "linkedin"), entry_of(row.stats, "alerts")
    assert (row.stats["new"], row.stats["duplicates"]) == (2, 1)
    assert guest["postings"] + mail["postings"] == 2
    assert guest.get("duplicates", 0) + mail.get("duplicates", 0) == 1
    assert (guest["outcome"], mail["outcome"]) == ("complete", "complete")


def test_real_feeds_and_boards_side_by_side_keep_their_own_entries(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, feeds_search(job_feeds=["arbeitnow"], company_boards=[ROBOTICS]))
    sweep_id = new_sweep(factory, alice)

    async def no_wait(_: float) -> None:
        return None

    feed = feeds.FeedRunner(
        factory,
        keys=keys.reader(factory, KEYRING),
        transport=fake_api().transport,
        sleep=no_wait,
        clock=now,
    )
    asyncio.run(
        sweep.run_sweep(factory, sweep_id, now, feeds=feed, apis=(boards_hook(fixture_answers()),))
    )
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    board, arbeitnow = entry_of(row.stats, "board"), entry_of(row.stats, "arbeitnow")
    assert (board["outcome"], arbeitnow["outcome"]) == ("complete", "complete")
    assert board["postings"] >= 1
    assert arbeitnow["postings"] >= 1
    with factory() as db:
        stored = dict(
            db.execute(
                sa.select(Job.source, sa.func.count())
                .where(Job.user_id == alice)
                .group_by(Job.source)
            ).all()
        )
    assert stored == {
        "ashby:example-robotics": board["postings"],
        "arbeitnow": arbeitnow["postings"],
    }
    assert row.stats["new"] == board["postings"] + arbeitnow["postings"]


# --- failures, cancellation, the time limit ------------------------------------------------------


def test_a_source_that_fails_ends_alone_and_the_others_complete(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    sweep_id = profile(factory, alice)
    log: list[str] = []
    together = asyncio.Barrier(3)
    asyncio.run(
        sweep.run_sweep(
            factory,
            sweep_id,
            now,
            alerts=source("alerts", log, together=together, error="mail_unavailable"),
            feeds=source(
                "arbeitnow",
                log,
                together=together,
                stores=(found("GenAI Engineer", "https://www.arbeitnow.com/jobs/1"),),
            ),
            apis=(source("board", log, together=together),),
            browser=source("custom", log, stores=(found("AI Engineer", f"https://{HOST}/jobs/2"),)),
        )
    )
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    assert entry_of(row.stats, "alerts")["outcome"] == "failed"
    assert entry_of(row.stats, "arbeitnow")["outcome"] == "complete"
    assert entry_of(row.stats, "board")["outcome"] == "no_matches"
    assert entry_of(row.stats, "custom")["outcome"] == "complete"
    assert row.stats["new"] == 2


def test_a_source_that_raises_lets_the_others_end_then_fails_the_sweep(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """As when it ran alone: a bug fails the sweep and no website starts, but only once the
    sources beside it stored what they found."""
    sweep_id = profile(factory, alice)
    log: list[str] = []
    together = asyncio.Barrier(2)
    started: list[str] = []

    async def the_websites(*_: Any) -> None:
        started.append("websites")

    asyncio.run(
        sweep.run_sweep(
            factory,
            sweep_id,
            now,
            alerts=source("alerts", log, together=together, raises=True),
            feeds=source(
                "arbeitnow",
                log,
                together=together,
                stores=(found("GenAI Engineer", "https://www.arbeitnow.com/jobs/1"),),
            ),
            browser=the_websites,
        )
    )
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERNAL)
    assert "arbeitnow ended" in log
    assert started == []
    assert entry_of(row.stats, "arbeitnow")["outcome"] == "complete"
    assert [job.title for job in jobs_of(factory, alice)] == ["GenAI Engineer"]


def hanging(kind: str, log: list[str], together: asyncio.Barrier) -> Hook:
    """A source that runs until it is cancelled, and says so."""

    async def hook(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float) -> None:
        entry = entry_of(stats, kind)
        entry.update(phase="running", outcome=None, reason=None)
        await meet(together)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            log.append(f"{kind} cancelled")
            raise

    return hook


def test_cancelling_the_sweep_cancels_every_source_and_leaves_no_task(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    sweep_id = profile(factory, alice)
    log: list[str] = []
    left: list[asyncio.Task[Any]] = []

    async def scenario() -> None:
        together = asyncio.Barrier(4)  # the three sources and this scenario
        task = asyncio.create_task(
            sweep.run_sweep(
                factory,
                sweep_id,
                now,
                alerts=hanging("alerts", log, together),
                feeds=hanging("arbeitnow", log, together),
                apis=(hanging("board", log, together),),
            )
        )
        await meet(together)
        task.cancel()  # the worker stops
        with pytest.raises(asyncio.CancelledError):
            await task
        left.extend(t for t in asyncio.all_tasks() if t is not asyncio.current_task())

    asyncio.run(scenario())
    assert sorted(log) == ["alerts cancelled", "arbeitnow cancelled", "board cancelled"]
    assert left == []
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)


def test_the_time_limit_stops_every_source(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    sweep_id = profile(factory, alice)
    log: list[str] = []

    async def scenario() -> None:
        together = asyncio.Barrier(3)
        await sweep.run_sweep(
            factory,
            sweep_id,
            now,
            timeout_s=0.5,
            alerts=hanging("alerts", log, together),
            feeds=hanging("arbeitnow", log, together),
            apis=(hanging("board", log, together),),
        )

    asyncio.run(scenario())
    assert sorted(log) == ["alerts cancelled", "arbeitnow cancelled", "board cancelled"]
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.TIMED_OUT)


# --- the writes ---------------------------------------------------------------------------------


def test_a_progress_write_carries_a_copy_taken_when_it_started(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sources go on changing the stats while a write runs in its thread: the write carries the
    stats as they were when it took the lock, and never the live object."""
    sweep_id = profile(factory, alice, interactive_sites=[])
    written: list[sweep.Stats] = []
    original = sweep.save_progress

    def recorded(sessions: Any, sweep_id_: uuid.UUID, stats: sweep.Stats, at: datetime) -> None:
        written.append(stats)
        original(sessions, sweep_id_, stats, at)

    monkeypatch.setattr(sweep, "save_progress", recorded)
    live: list[sweep.Stats] = []

    async def changing(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        live.append(stats)
        entry = entry_of(stats, "board")
        entry["listed"] = 1
        await progress()
        entry["listed"] = 2  # after the write took its copy

    asyncio.run(sweep.run_sweep(factory, sweep_id, now, apis=(changing,)))
    assert written
    assert all(copy is not live[0] for copy in written)
    assert entry_of(written[0], "board")["listed"] == 1


def test_one_source_settling_neither_waits_for_nor_closes_another(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep_id = profile(factory, alice, interactive_sites=[])
    plan = sweep.claim(factory, sweep_id, NOW)
    assert plan is not None
    inside, release = threading.Event(), threading.Event()
    original = sweep.commit_verified

    def held(*args: Any) -> Any:
        inside.set()
        release.wait(30)
        return original(*args)

    monkeypatch.setattr(sweep, "commit_verified", held)
    stats = sweep.Stats(sources=sweep.initial_sources(plan.search_scope))
    commits = sweep.Commits(factory, plan, stats, now, drain_s=0.05)
    first = found("GenAI Engineer", "https://www.arbeitnow.com/jobs/1")
    second = found("AI Engineer", "https://www.arbeitnow.com/jobs/2")

    async def scenario() -> None:
        commits.admit("feed")
        commits.admit("board")
        saving = asyncio.create_task(
            commits.save("feed", "arbeitnow", finder.VerifiedBatch((first,)))
        )
        await asyncio.to_thread(inside.wait, 30)
        # The board ends while the feed's commit is held: it waits for nothing of the feed's.
        assert await commits.settle("board")
        with pytest.raises(sweep.StoreRefusedError):
            await commits.save("board", "x", finder.VerifiedBatch((second,)))
        release.set()
        await saving
        await commits.save("feed", "arbeitnow", finder.VerifiedBatch((second,)))  # still admitted
        assert await commits.settle("feed")
        assert await commits.settle(close=True)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert not commits.unsettled
    assert sorted(job.title for job in jobs_of(factory, alice)) == ["AI Engineer", "GenAI Engineer"]


def test_a_commit_the_database_holds_is_still_given_up_after_the_drain(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep_id = profile(factory, alice, interactive_sites=[])
    plan = sweep.claim(factory, sweep_id, NOW)
    assert plan is not None
    inside, release = threading.Event(), threading.Event()

    def held(*args: Any) -> Any:
        inside.set()
        release.wait(30)
        return None

    monkeypatch.setattr(sweep, "commit_verified", held)
    commits = sweep.Commits(factory, plan, sweep.Stats(), now, drain_s=0.05)

    async def scenario() -> bool:
        commits.admit("feed")
        saving = asyncio.create_task(
            commits.save(
                "feed", "arbeitnow", finder.VerifiedBatch((found("A", "https://a.test/1"),))
            )
        )
        await asyncio.to_thread(inside.wait, 30)
        settled = await commits.settle("feed")
        release.set()
        with pytest.raises(sweep.StoreRefusedError):
            await saving
        return settled

    try:
        assert asyncio.run(scenario()) is False
    finally:
        release.set()
    assert commits.unsettled


def test_parses_of_a_sweep_run_one_at_a_time() -> None:
    order: list[str] = []
    inside, release = threading.Event(), threading.Event()

    def first() -> str:
        order.append("first starts")
        inside.set()
        release.wait(30)
        order.append("first ends")
        return "a"

    def second() -> str:
        order.append("second starts")
        return "b"

    async def scenario() -> list[str]:
        parsing = asyncio.Semaphore(sweep.PARSES_AT_ONCE)
        one = asyncio.create_task(sweep.parse_one(parsing, first))
        await asyncio.to_thread(inside.wait, 30)
        two = asyncio.create_task(sweep.parse_one(parsing, second))
        await asyncio.sleep(0)
        assert parsing.locked()
        assert not two.done()
        release.set()
        return [await one, await two]

    try:
        assert asyncio.run(scenario()) == ["a", "b"]
    finally:
        release.set()
    assert order == ["first starts", "first ends", "second starts"]


# --- the worker ---------------------------------------------------------------------------------


def test_the_worker_runs_linkedins_public_search_beside_the_api_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[dict[str, Any]] = []

    async def recorded(*args: Any, **options: Any) -> None:
        given.append(options)

    monkeypatch.setattr(sweep, "run_sweep", recorded)
    alerts, feed = websites(), websites()
    runner = website_runner(tools=None)
    ctx: dict[str, Any] = {
        "sessions": None,
        "clock": now,
        "alert_sources": alerts,
        "feed_sources": feed,
        "browser_sources": runner,
    }
    asyncio.run(worker.run_sweep(ctx, str(uuid.uuid4())))
    [options] = given
    assert options["alerts"] is alerts
    assert options["feeds"] is feed
    board, linkedin = options["apis"]
    assert isinstance(board, boards.Boards)
    assert linkedin == runner.linkedin
    assert options["browser"] == runner.websites
    # A test's own website hook stays one hook, run after the API sources.
    plain = websites()
    asyncio.run(worker.run_sweep(ctx | {"browser_sources": plain}, str(uuid.uuid4())))
    assert given[1]["apis"][1] is None
    assert given[1]["browser"] is plain
