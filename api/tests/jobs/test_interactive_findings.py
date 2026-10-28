"""Jobs J11 and J11b: what the interactive finder verified on a job website is stored in the
person's list as it is reported, through the real sweep, runner and selected source (sweep.Commits,
app/browser/sources/interactive.py), with a scripted finder and fake browser tools: never a model
or a real website."""

import asyncio
import json
import random
import threading
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import Application, Authorisation
from app.browser import finder
from app.browser.sources import MemoryPacer, Runner
from app.jobs import sweep
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus, Sweep, SweepStatus
from tests.browser.fakes import FakeLeases, ScriptedFinder
from tests.jobs.sweep_helpers import jobs_of, new_sweep, now, read_sweep, search, set_profile

BASE = "https://jobs.example.test/"
HOST = "jobs.example.test"
FIRST = '- searchbox "Query" [ref=e1]\n- button "Search" [ref=e3]'
# A planted marker: the model's own words, which must never reach a job or an event.
MODEL_WORDS = "e2e-model-words-4f19c2"
INSTRUCTIONS = "e2e-instructions-8d03aa: prefer robots"


def detail(title: str, *lines: str) -> str:
    # browserd/pages.py's snapshot shape, as tests/browser/test_verified_findings.py writes it.
    return f'- main [ref=e1]:\n  - heading "{title}" [level=1] [ref=e2]\n' + "\n".join(
        f"  - paragraph [ref=e{index + 3}]: {line}" for index, line in enumerate(lines)
    )


PAGES = {
    "jobs/robot": detail(
        "Robot Engineer",
        "Employer: Example Robotics",
        "Location: Paris, France",
        "Published: 2026-10-20",
        "Build the planners of robot fleets in Python.",
    ),
    "jobs/arm": detail("Arm Engineer", "Tune robot arms in the lab."),
    "jobs/arm-2": detail("Arm Engineer", "Tune robot arms on site."),
    "jobs/cyber": detail("Cyber Defence Engineer", "Employer: Example Robotics", "Guard robots."),
    "jobs/sales": detail("Robot Sales Lead", "Employer: Example Robotics", "Sell robots."),
}


class SiteTools:
    """The invented job website: a search box, results after Search, and detail pages."""

    def __init__(self) -> None:
        links = "\n".join(
            f'- link "{path}" [ref=e{index + 5}]\n  - /url: /{path}'
            for index, path in enumerate(PAGES)
        )
        self.pages = {BASE: FIRST, BASE + "search": links} | {
            BASE + path: text for path, text in PAGES.items()
        }
        self.current = BASE
        self.typed = ""
        self.actions: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        args.pop("_discovery_scope")
        self.actions.append(name)
        if name == "browser_navigate":
            if args["url"] not in self.pages:
                return {"ok": False, "error": "navigation_failed"}
            self.current = args["url"]
        elif name == "browser_type":
            self.typed = args["text"]
        elif name == "browser_click":
            self.current = BASE + "search"
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        text = self.pages[self.current]
        if self.current == BASE + "search":
            # The results keep the search box with the title typed (finder._note).
            text = (
                f'- searchbox "Query" [ref=e1]: {self.typed}\n- button "Search" [ref=e3]\n' + text
            )
        return {"ok": True, "url": self.current, "snapshot": text}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def title_of(path: str) -> str:
    return PAGES[path].split('heading "', 1)[1].split('"', 1)[0]


async def searched(session: finder.FinderSession) -> None:
    assert not (await session.interact("browser_type", {"ref": "e1", "text": "robot"}))[1]
    assert not (await session.interact("browser_click", {"ref": "e3"}))[1]


async def report(session: finder.FinderSession, *paths: str) -> str:
    """Open each detail page and report it, one report per page (the session keeps the last
    detail page read only), as the model does until a tool tells it to stop."""
    for path in paths:
        assert not (await session.navigate("/" + path))[1]
        text, error = await session.report_verified(
            [{"title": title_of(path), "url": "/" + path, "text": MODEL_WORDS}]
        )
        if error:
            return text
        assert json.loads(text)["kept"] == 1
    return ""


async def again(session: finder.FinderSession, path: str, title: str = "") -> str:
    """Open a detail page the run reported before and report it again: the refusal's reason."""
    assert not (await session.navigate("/" + path))[1]
    text, error = await session.report_verified(
        [{"title": title or title_of(path), "url": "/" + path, "text": MODEL_WORDS}]
    )
    assert not error, text
    answer = json.loads(text)
    assert answer["kept"] == 0, text
    return answer["refused"][0]["reason"]


def reports(*paths: str) -> Callable[[finder.FinderSession], Any]:
    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        await report(session, *paths)
        return finder.Ran()

    return script


def profile(factory: sessionmaker[Session], user: uuid.UUID, **changes: Any) -> None:
    set_profile(
        factory,
        user,
        search(
            **{
                "title_include": {"terms": ["robot"], "advanced_regex": False},
                "location": {"terms": [], "advanced_regex": False},
                # Not "sales": an unwanted title is dropped before the store (pull request
                # 715), and the sales posting here tests the never apply rule.
                "title_exclude": {"terms": ["account executive"], "advanced_regex": False},
                "never_apply": ["No sales roles: sales (test rule)"],
                "interactive_sites": [
                    {"platform": "custom", "url": BASE, "enabled": True, "mode": "llm_browser"}
                ],
                "interactive_instructions": INSTRUCTIONS,
                **changes,
            }
        ),
    )


def runner(script: Callable[[finder.FinderSession], Any], tools: SiteTools | None = None) -> Runner:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Runner(
        FakeLeases(),  # type: ignore[arg-type]
        lambda *_: tools or SiteTools(),
        sleep=sleep,
        rng=random.Random(3),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
        finder=ScriptedFinder(script),
    )


def run(factory: sessionmaker[Session], sweep_id: uuid.UUID, hook: Runner) -> Sweep:
    asyncio.run(sweep.run_sweep(factory, sweep_id, now, browser=hook))
    return read_sweep(factory, sweep_id)


def events_of(factory: sessionmaker[Session], user: uuid.UUID) -> list[JobEvent]:
    with factory() as db:
        return list(db.scalars(sa.select(JobEvent).where(JobEvent.user_id == user)))


def entry_of(row: Sweep) -> dict[str, Any]:
    [entry] = [s for s in row.stats["sources"] if s.get("mode") == "llm_browser"]
    return entry


def assert_counts_agree(factory: sessionmaker[Session], row: Sweep, user: uuid.UUID) -> None:
    """The row's counters are those of the jobs this sweep stored."""
    with factory() as db:
        stored = db.scalars(
            sa.select(JobEvent.job_id).where(
                JobEvent.user_id == user,
                JobEvent.kind == JobEventKind.sweep,
                JobEvent.data["sweep_id"].astext == str(row.id),
            )
        ).all()
        skipped = db.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(Job.id.in_(stored), Job.status == JobStatus.skipped)
        )
    assert row.stats["new"] == len(stored) == entry_of(row)["postings"]
    assert row.stats["skipped_by_rule"] == skipped == entry_of(row)["skipped_by_rule"]


# --- stored as every new posting is -------------------------------------------------------------


def test_verified_findings_become_scored_found_jobs_with_their_observed_facts_only(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    row = run(factory, new_sweep(factory, alice), runner(reports("jobs/robot", "jobs/arm")))
    assert row.status is SweepStatus.done
    entry = entry_of(row)
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 2, 0)
    assert (entry["outcome"], entry["reason"], entry["error"]) == ("complete", None, None)
    assert (row.stats["new"], row.stats["duplicates"]) == (2, 0)
    arm, robot = jobs_of(factory, alice)  # ordered by company: "Employer not stated" first
    assert robot.company == "Example Robotics"
    assert robot.location == "Paris, France"
    assert robot.published_on == date(2026, 10, 20)
    assert "planners of robot fleets" in (robot.posting_text or "")
    # Unknown stays unknown: no website name as employer, no date made up from today.
    assert arm.company == sweep.UNKNOWN_EMPLOYER
    assert arm.published_on is None
    assert arm.location == ""
    # No pay and no work mode stated: none is made up.
    assert (arm.pay_min, arm.pay_max, arm.pay_currency, arm.pay_period) == (None,) * 4
    assert arm.work_mode is None
    for job in (robot, arm):
        assert job.status is JobStatus.found
        assert job.source == HOST
        assert job.fit is not None  # scored against the skills, as a board posting is
        assert job.legacy == {}
        assert (job.url or "").startswith(BASE)
        assert MODEL_WORDS not in (job.posting_text or "") + (job.description or "")
    discovery = {
        event.job_id: event.data["discovery"]
        for event in events_of(factory, alice)
        if event.kind is JobEventKind.sweep
    }
    assert discovery[robot.id]["evidence"] == {
        "title": "Robot Engineer",
        "company": "Employer: Example Robotics",
        "published_on": "Published: 2026-10-20",
        "location": "Location: Paris, France",
    }
    assert discovery[robot.id]["unknown"] == []
    assert discovery[arm.id]["unknown"] == ["company", "published_on", "location"]
    assert discovery[arm.id]["source_id"] == entry["source_id"]
    # The frozen search scope of the row holds the instructions by design (J9); nothing else.
    every = json.dumps([e.data for e in events_of(factory, alice)] + row.stats["sources"])
    for private in (MODEL_WORDS, INSTRUCTIONS, "untrusted_page_snapshot", "tune robot arms"):
        assert private not in every
    assert_counts_agree(factory, row, alice)


def test_nothing_but_found_or_skipped_jobs_is_written(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    run(factory, new_sweep(factory, alice), runner(reports("jobs/robot", "jobs/sales")))
    with factory() as db:
        for model in (Application, Authorisation):
            assert db.scalar(sa.select(sa.func.count()).select_from(model)) == 0
    kinds = {event.kind for event in events_of(factory, alice)}
    assert kinds <= {
        JobEventKind.created,
        JobEventKind.sweep,
        JobEventKind.fit,
        JobEventKind.status,
        JobEventKind.note,
    }
    assert {job.status for job in jobs_of(factory, alice)} == {JobStatus.found, JobStatus.skipped}


def test_never_apply_rules_and_the_cybersecurity_rule_skip_with_their_note(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)  # the person's rules name sales only
    row = run(
        factory,
        new_sweep(factory, alice),
        runner(reports("jobs/cyber", "jobs/sales", "jobs/robot")),
    )
    by_title = {job.title: job for job in jobs_of(factory, alice)}
    assert by_title["Cyber Defence Engineer"].status is JobStatus.skipped
    assert "cyber" in by_title["Cyber Defence Engineer"].notes
    assert by_title["Robot Sales Lead"].status is JobStatus.skipped
    assert by_title["Robot Sales Lead"].notes == "No sales roles: sales (test rule)"
    assert by_title["Robot Engineer"].status is JobStatus.found
    assert (row.stats["skipped_by_rule"], entry_of(row)["skipped_by_rule"]) == (2, 2)
    assert row.stats["new"] == 3
    assert_counts_agree(factory, row, alice)


# --- duplicates ---------------------------------------------------------------------------------


def add_job(factory: sessionmaker[Session], user: uuid.UUID, **values: Any) -> uuid.UUID:
    job_id = uuid.uuid4()
    with factory() as db, db.begin():
        db.add(
            Job(
                id=job_id,
                user_id=user,
                slug=values.pop("slug", f"kept-{job_id.hex[:8]}"),
                status=JobStatus.shortlisted,
                found_on=date(2026, 10, 1),
                notes="mine",
                **values,
            )
        )
    return job_id


def test_duplicates_by_address_or_identity_name_the_website_and_keep_the_row(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    profile(factory, alice)
    by_url = add_job(
        factory, alice, company="Example Robotics", title="Old title", location="",
        url=BASE + "jobs/robot", source="ashby:example",
    )  # fmt: skip
    by_identity = add_job(
        factory, alice, company="Example Robotics", title="Cyber Defence Engineer", location="",
        url="https://boards.example.org/cyber", source="greenhouse:example",
    )  # fmt: skip
    # Bob's row with the same address is his own: Alice's posting is new to her, his untouched.
    bobs = add_job(factory, bob, company="Bob Co", title="Kept", location="", url=BASE + "jobs/arm")
    row = run(
        factory,
        new_sweep(factory, alice),
        runner(reports("jobs/robot", "jobs/cyber", "jobs/arm")),
    )
    entry = entry_of(row)
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (3, 1, 2)
    assert (row.stats["new"], row.stats["duplicates"]) == (1, 2)
    with factory() as db:
        mine = db.get(Job, by_url)
        assert mine is not None
        assert (mine.title, mine.status, mine.notes) == ("Old title", JobStatus.shortlisted, "mine")
        assert mine.source == f"ashby:example, {HOST}"
        other = db.get(Job, by_identity)
        assert other is not None
        assert other.source == f"greenhouse:example, {HOST}"
        his = db.get(Job, bobs)
        assert his is not None
        assert (his.source, his.company) == (None, "Bob Co")
    assert len(jobs_of(factory, alice)) == 3
    assert_counts_agree(factory, row, alice)


def test_same_title_with_unknown_employers_is_two_postings_and_a_second_run_finds_duplicates(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    first = run(factory, new_sweep(factory, alice), runner(reports("jobs/arm", "jobs/arm-2")))
    assert first.stats["new"] == 2
    assert [job.company for job in jobs_of(factory, alice)] == [sweep.UNKNOWN_EMPLOYER] * 2
    again = run(factory, new_sweep(factory, alice), runner(reports("jobs/arm", "jobs/arm-2")))
    assert (again.stats["new"], again.stats["duplicates"]) == (0, 2)
    assert (entry_of(again)["found"], entry_of(again)["outcome"]) == (2, "complete")
    assert len(jobs_of(factory, alice)) == 2


def test_a_row_stored_meanwhile_by_another_request_is_the_duplicate_it_is(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan's Known is loaded at claim; a row stored after (another request) is met by the
    insert's conflict, looked up among Alice's rows, and named a duplicate with this source."""
    profile(factory, alice)
    original = sweep.commit_verified
    raced: list[uuid.UUID] = []

    def racing(sessions: Any, plan: Any, *args: Any) -> Any:
        if not raced:
            stored = add_job(
                factory, alice, company="Example Robotics", title="Robot Engineer",
                location="Paris, France", url=BASE + "jobs/robot", source="site:example",
            )  # fmt: skip
            raced.append(stored)
        return original(sessions, plan, *args)

    monkeypatch.setattr(sweep, "commit_verified", racing)
    row = run(factory, new_sweep(factory, alice), runner(reports("jobs/robot", "jobs/arm")))
    assert (row.stats["new"], row.stats["duplicates"]) == (1, 1)
    with factory() as db:
        winner = db.get(Job, raced[0])
        assert winner is not None
        assert (winner.status, winner.notes) == (JobStatus.shortlisted, "mine")
        assert winner.source == f"site:example, {HOST}"
    assert len(jobs_of(factory, alice)) == 2
    assert_counts_agree(factory, row, alice)


# --- failures, cancellation and the end of the row ---------------------------------------------


def test_a_failed_batch_stores_nothing_of_itself_and_keeps_the_batches_before(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile(factory, alice)
    original = sweep._store_finding
    calls: list[str] = []

    def failing(db: Session, plan: Any, source_id: str, source: str, finding: Any, *rest: Any):
        calls.append(finding.title)
        original(db, plan, source_id, source, finding, *rest)
        if len(calls) == 3:
            raise RuntimeError("the database went away")  # after the row was inserted

    monkeypatch.setattr(sweep, "_store_finding", failing)
    row = run(
        factory,
        new_sweep(factory, alice),
        runner(reports("jobs/robot", "jobs/arm", "jobs/sales", "jobs/cyber")),
    )
    # Two batches stored, the third rolled back whole, and the source stopped there.
    assert sorted(job.title for job in jobs_of(factory, alice)) == [
        "Arm Engineer",
        "Robot Engineer",
    ]
    assert calls == ["Robot Engineer", "Arm Engineer", "Robot Sales Lead"]
    entry = entry_of(row)
    assert (entry["found"], entry["postings"], entry["error"]) == (2, 2, "store_failed")
    assert entry["outcome"] == "partial"
    assert row.status is SweepStatus.done
    assert_counts_agree(factory, row, alice)


def test_an_agent_failure_after_a_batch_keeps_what_was_stored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        await report(session, "jobs/robot")
        raise RuntimeError("the model stopped")

    row = run(factory, new_sweep(factory, alice), runner(script))
    entry = entry_of(row)
    assert (entry["found"], entry["postings"], entry["outcome"]) == (1, 1, "partial")
    assert entry["error"] == "agent_failed"
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]
    assert_counts_agree(factory, row, alice)


def test_cancelling_the_sweep_mid_source_keeps_the_stored_jobs_and_their_counts(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    reported = asyncio.Event()
    never = asyncio.Event()

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        await report(session, "jobs/robot")
        reported.set()
        await never.wait()  # the model's next turn, when the worker stops
        raise AssertionError("cancelled before")

    sweep_id = new_sweep(factory, alice)

    async def scenario() -> None:
        task = asyncio.create_task(sweep.run_sweep(factory, sweep_id, now, browser=runner(script)))
        await reported.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)
    assert row.stats["new"] == 1
    assert (entry_of(row)["found"], entry_of(row)["postings"]) == (1, 1)
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]
    assert_counts_agree(factory, row, alice)


class Held:
    """Holds the commit's thread inside its transaction (after the row lock and the insert,
    before COMMIT) until released."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.inside, self.release = threading.Event(), threading.Event()
        original = sweep._counted

        def held(stats: sweep.Stats, *args: Any) -> None:
            original(stats, *args)
            if threading.current_thread() is not threading.main_thread():
                self.inside.set()
                assert self.release.wait(30), "the test never released the commit"

        monkeypatch.setattr(sweep, "_counted", held)


def test_a_commit_whose_tool_call_is_cancelled_finishes_before_the_next_source(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model's tool call is torn down while its batch is inside the database: the commit
    still ends and is counted, before the next source starts and before the sweep ends."""
    second = "https://second.example.test/"
    profile(
        factory,
        alice,
        interactive_sites=[
            {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
            for url in (BASE, second)
        ],
    )
    held = Held(monkeypatch)
    seen_at_second_start: list[tuple[int, int]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        if session.start == second:
            seen_at_second_start.append((stats_of["run"].new, len(session.reported_urls)))
            return finder.Ran()
        await searched(session)
        assert not (await session.navigate("/jobs/robot"))[1]
        call = asyncio.create_task(
            session.report_verified(
                [{"title": "Robot Engineer", "url": "/jobs/robot", "text": MODEL_WORDS}]
            )
        )
        await asyncio.to_thread(held.inside.wait, 30)
        call.cancel()  # the SDK tears the tool call down
        with pytest.raises(asyncio.CancelledError):
            await call
        return finder.Ran()

    original_settle = sweep.Commits.settle
    stats_of: dict[str, sweep.Stats] = {}

    async def settle(self: sweep.Commits, *, close: bool = False) -> bool:
        stats_of["run"] = self.stats
        held.release.set()  # the commit's thread may now end
        return await original_settle(self, close=close)

    monkeypatch.setattr(sweep.Commits, "settle", settle)
    tools = SiteTools()
    tools.pages[second] = FIRST
    try:
        row = run(factory, new_sweep(factory, alice), runner(script, tools))
    finally:
        held.release.set()
    assert row.status is SweepStatus.done
    assert seen_at_second_start == [(1, 0)]  # counted before the second website began
    first, _ = (s for s in row.stats["sources"] if s.get("mode") == "llm_browser")
    assert (first["found"], first["postings"]) == (1, 1)
    assert row.stats["new"] == 1
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]


def test_a_commit_that_outlives_the_wait_leaves_the_row_to_recovery_with_its_counts(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile(factory, alice)
    held = Held(monkeypatch)
    monkeypatch.setattr(sweep, "DRAIN_S", 0.0)
    published: list[int] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        assert not (await session.navigate("/jobs/robot"))[1]
        call = asyncio.create_task(
            session.report_verified(
                [{"title": "Robot Engineer", "url": "/jobs/robot", "text": MODEL_WORDS}]
            )
        )
        await asyncio.to_thread(held.inside.wait, 30)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return finder.Ran()

    sweep_id = new_sweep(factory, alice)

    async def scenario() -> None:
        hook = runner(script)
        await sweep.run_sweep(factory, sweep_id, now, browser=hook)
        # The run let go of the row without an end. The commit now ends: its jobs and the
        # row's counters land together, and nothing is published to the finished run.
        row = read_sweep(factory, sweep_id)
        assert row.status is SweepStatus.running
        held.release.set()
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*left, return_exceptions=True)
        published.append(len(left))

    try:
        asyncio.run(scenario())
    finally:
        held.release.set()
    assert published == [1]  # the commit's own task, nothing else
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.running
    assert (row.stats["new"], entry_of(row)["postings"]) == (1, 1)
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]
    # The existing recovery ends it, keeping the committed counts.
    with factory() as db:
        assert sweep.reap_stale(db, now() + timedelta(minutes=5)) == 1
        db.commit()
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.CRASHED)
    assert row.stats["new"] == 1
    assert_counts_agree(factory, row, alice)


def test_a_row_ended_by_another_finalizer_first_refuses_the_batch(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    sweep_id = new_sweep(factory, alice)

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        await asyncio.to_thread(sweep.fail_sweep, factory, sweep_id, sweep.CRASHED, now())
        assert not (await session.navigate("/jobs/robot"))[1]
        text, error = await session.report_verified(
            [{"title": "Robot Engineer", "url": "/jobs/robot", "text": MODEL_WORDS}]
        )
        assert error
        assert "store_failed" in text
        return finder.Ran()

    row = run(factory, sweep_id, runner(script))
    assert (row.status, row.error) == (SweepStatus.failed, sweep.CRASHED)
    assert jobs_of(factory, alice) == []
    assert row.stats.get("new", 0) == 0


def test_a_batch_after_its_source_settled_starts_no_commit(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_: Any) -> None:
        raise AssertionError("a refused batch reached the database")

    monkeypatch.setattr(sweep, "commit_verified", never)
    plan: Any = type("P", (), {"known": None})()
    commits = sweep.Commits(factory, plan, sweep.Stats(), now)
    found = finder.VerifiedFinding("Robot Engineer", "", BASE + "x", "x", None, None, {}, "")
    batch = finder.VerifiedBatch((found,))

    async def scenario() -> None:
        with pytest.raises(sweep.StoreRefusedError):
            await commits.save("source-a", HOST, batch)  # never admitted
        commits.admit("source-a")
        with pytest.raises(sweep.StoreRefusedError):
            await commits.save("source-b", HOST, batch)  # another source's batch
        assert await commits.settle()
        with pytest.raises(sweep.StoreRefusedError):
            await commits.save("source-a", HOST, batch)  # its source ended
        assert await commits.settle(close=True)
        commits.admit("source-a")
        with pytest.raises(sweep.StoreRefusedError):
            await commits.save("source-a", HOST, batch)  # the run closed

    asyncio.run(scenario())


def report_held(session: finder.FinderSession) -> "asyncio.Task[tuple[str, bool]]":
    return asyncio.create_task(
        session.report_verified(
            [{"title": "Robot Engineer", "url": "/jobs/robot", "text": MODEL_WORDS}]
        )
    )


def test_a_commit_whose_answer_was_lost_is_read_again_never_stored_twice(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The COMMIT landed and its answer was lost: the batch is not retried, the source stops,
    and the run takes the row's counts and the person's list as stored, so the counts written
    after (progress, the end) still agree with the jobs."""
    profile(factory, alice)
    original = sweep.commit_verified
    calls: list[int] = []

    def lost(*args: Any) -> Any:
        calls.append(1)
        original(*args)
        raise RuntimeError("the answer of COMMIT was lost")

    monkeypatch.setattr(sweep, "commit_verified", lost)
    original_settle = sweep.Commits.settle
    known: list[set[str]] = []

    async def settle(self: sweep.Commits, *, close: bool = False) -> bool:
        known.append(set(self.plan.known.urls))
        return await original_settle(self, close=close)

    monkeypatch.setattr(sweep.Commits, "settle", settle)
    row = run(factory, new_sweep(factory, alice), runner(reports("jobs/robot", "jobs/arm")))
    assert calls == [1]  # never retried, and the source stopped there
    entry = entry_of(row)
    assert (entry["found"], entry["postings"], entry["error"]) == (1, 1, "store_failed")
    assert (row.status, row.stats["new"]) == (SweepStatus.done, 1)
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]
    # A later source finds it a duplicate (Known holds its addresses in their key form).
    assert [url for url in known[0] if url.endswith("//jobs.example.test/jobs/robot")]
    assert_counts_agree(factory, row, alice)


def test_a_finalizer_waiting_on_a_held_commit_ends_the_row_with_its_counts(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage first: another finalizer asks for the row while a commit holds its lock. It ends
    the row after the COMMIT, with the counts of the jobs stored, and the run writes no end."""
    profile(factory, alice)
    held = Held(monkeypatch)
    sweep_id = new_sweep(factory, alice)

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        assert not (await session.navigate("/jobs/robot"))[1]
        call = report_held(session)
        await asyncio.to_thread(held.inside.wait, 30)
        ender = asyncio.create_task(
            asyncio.to_thread(sweep.fail_sweep, factory, sweep_id, sweep.CRASHED, now())
        )
        held.release.set()
        text, error = await call
        await ender
        assert (json.loads(text)["kept"], error) == (1, False)
        return finder.Ran()

    try:
        row = run(factory, sweep_id, runner(script))
    finally:
        held.release.set()
    assert (row.status, row.error) == (SweepStatus.failed, sweep.CRASHED)
    assert (row.stats["new"], entry_of(row)["postings"], entry_of(row)["found"]) == (1, 1, 1)
    assert [job.title for job in jobs_of(factory, alice)] == ["Robot Engineer"]
    assert_counts_agree(factory, row, alice)


def test_a_second_cancellation_during_the_drain_leaves_the_row_to_recovery(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile(factory, alice)
    held = Held(monkeypatch)
    sweep_id = new_sweep(factory, alice)
    never = asyncio.Event()

    async def script(session: finder.FinderSession) -> finder.Ran:
        await searched(session)
        assert not (await session.navigate("/jobs/robot"))[1]
        await report_held(session)
        await never.wait()
        raise AssertionError("cancelled before")

    original_settle = sweep.Commits.settle
    runs: list[sweep.Commits] = []
    draining = asyncio.Event()

    async def settle(self: sweep.Commits, *, close: bool = False) -> bool:
        runs.append(self)
        draining.set()
        return await original_settle(self, close=close)

    monkeypatch.setattr(sweep.Commits, "settle", settle)

    async def scenario() -> None:
        task = asyncio.create_task(sweep.run_sweep(factory, sweep_id, now, browser=runner(script)))
        try:
            await asyncio.to_thread(held.inside.wait, 30)
            task.cancel()  # the worker stops: the run waits for the commit
            await draining.wait()
            task.cancel()  # and is stopped again while it waits
            with pytest.raises(asyncio.CancelledError):
                await task
            assert read_sweep(factory, sweep_id).status is SweepStatus.running
        finally:
            held.release.set()
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*left, return_exceptions=True)

    asyncio.run(scenario())
    [commits] = runs
    assert commits.unsettled
    assert commits.stats.new == 0  # nothing published to the run it let go of
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.stats["new"]) == (SweepStatus.running, 1)
    with factory() as db:
        assert sweep.reap_stale(db, now() + timedelta(minutes=5)) == 1
        db.commit()
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error, row.stats["new"]) == (SweepStatus.failed, sweep.CRASHED, 1)
    assert_counts_agree(factory, row, alice)


def test_a_progress_write_and_a_commit_never_overlap(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A progress write's thread holds the database; a batch reported meanwhile waits for that
    thread to end, even when the progress write's caller is cancelled."""
    profile(factory, alice)
    sweep_id = new_sweep(factory, alice)
    plan = sweep.claim(factory, sweep_id, now())
    assert plan is not None
    stats = sweep.Stats(sources=sweep.initial_sources(plan.search_scope))
    commits = sweep.Commits(factory, plan, stats, now)
    order: list[str] = []
    inside, release = threading.Event(), threading.Event()
    original = sweep.commit_verified

    def committing(*args: Any) -> Any:
        order.append("commit")
        return original(*args)

    monkeypatch.setattr(sweep, "commit_verified", committing)

    def write() -> None:
        inside.set()
        assert release.wait(30), "the test never released the progress write"
        sweep.save_progress(factory, sweep_id, stats, now())
        order.append("progress")

    found = finder.VerifiedFinding(
        "Robot Engineer", "", BASE + "jobs/robot", "Build robots.", None, None,
        {"title": "Robot Engineer"}, now().isoformat(),
    )  # fmt: skip
    source_id = stats.sources[0]["source_id"]

    async def scenario() -> None:
        writing = asyncio.create_task(commits.progress(lambda: asyncio.to_thread(write)))
        try:
            await asyncio.to_thread(inside.wait, 30)
            commits.admit(source_id)
            saving = asyncio.create_task(
                commits.save(source_id, HOST, finder.VerifiedBatch((found,)))
            )
            writing.cancel()
            for _ in range(20):
                await asyncio.sleep(0)
            assert order == []  # the batch waits for the progress thread
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await writing
        await saving
        assert await commits.settle(close=True)

    asyncio.run(scenario())
    assert order == ["progress", "commit"]
    assert (stats.new, read_sweep(factory, sweep_id).stats["new"]) == (1, 1)


def test_the_searches_of_a_website_store_each_posting_once_and_a_refusal_keeps_them(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """One agent per website (jobs J18), a search per title in one run: the verified batches
    reach the store as they come, a posting both searches give is stored once (its second
    report is merged as a duplicate), an unwanted title is dropped before the store, and the
    refusal in the second search keeps what both saved."""
    profile(
        factory,
        alice,
        title_include={"terms": ["robot", "arm"], "advanced_regex": False},
        title_exclude={"terms": ["sales"], "advanced_regex": False},
    )
    queries: list[str] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        assert session.interactive is not None
        for index, query in enumerate(session.interactive.titles):
            queries.append(query)
            if index:  # back to the results, whose search box and button it then uses
                assert not (await session.navigate("/search"))[1]
            assert not (await session.interact("browser_type", {"ref": "e1", "text": query}))[1]
            assert not (await session.interact("browser_click", {"ref": "e3"}))[1]
            if query == "robot":
                assert await report(session, "jobs/robot", "jobs/arm") == ""
                continue
            assert await again(session, "jobs/arm") == "duplicate"
            assert await report(session, "jobs/arm-2", "jobs/sales") == ""
            # The posting's Send application button: browserd refuses the click (a POST form).
            assert (await session.interact("browser_click", {"ref": "e99"}))[1]
        return finder.Ran()

    class Refusing(SiteTools):
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if args.get("ref") == "e99":
                return {"ok": False, "error": "unsupported_discovery"}
            return await super().call(name, args)

    row = run(factory, new_sweep(factory, alice), runner(script, Refusing()))
    assert queries == ["robot", "arm"]
    entry = entry_of(row)
    assert (entry["searches"], entry["found"], entry["postings"]) == (2, 3, 3)
    assert entry["searched"] == [{"title": "robot", "place": None}, {"title": "arm", "place": None}]
    assert (entry["unwanted"], entry.get("duplicates", 0)) == (1, 0)
    # The refused click is counted, and the website, which gave verified postings, was searched
    # (2026-10-28: how it ends is what was read, interactive.outcome).
    assert (entry["error"], entry["outcome"]) == (None, "complete")
    assert entry["refused"]["unsupported_interaction"] == 1
    assert (row.stats["new"], row.stats["duplicates"]) == (3, 0)
    assert sorted(job.title for job in jobs_of(factory, alice)) == [
        "Arm Engineer",
        "Arm Engineer",
        "Robot Engineer",
    ]
    assert_counts_agree(factory, row, alice)


def test_unwanted_places_other_contracts_and_an_address_read_two_ways_never_reach_the_store(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Review of pull request 715, follow ups 2: the unwanted titles and places and the several
    contracts are applied to each verified batch before it is saved, not only in the counts; an
    address one search reads as unwanted and the next as wanted is never saved and counts once
    (unwanted wins, fail closed)."""
    profile(
        factory,
        alice,
        title_include={"terms": ["robot", "arm"], "advanced_regex": False},
        title_exclude={"terms": ["sales"], "advanced_regex": False},
        location_exclude={"terms": ["Lyon"], "advanced_regex": False},
        contract_types=["CDI", "Stage"],
    )
    paths = ("jobs/robot", "jobs/lyon", "jobs/cdd", "jobs/flip")
    read: list[str] = []

    class TwoWays(SiteTools):
        """jobs/flip is a sales role the first time it is read, an engineer's the next."""

        def __init__(self) -> None:
            super().__init__()
            self.pages[BASE + "search"] = "\n".join(
                f'- link "{path}" [ref=e{index + 5}]\n  - /url: /{path}'
                for index, path in enumerate(paths)
            )
            self.pages[BASE + "jobs/lyon"] = detail("Robot Engineer", "Location: Lyon, France")
            self.pages[BASE + "jobs/cdd"] = detail("Robot Engineer CDD", "Build robot fleets.")
            self.pages[BASE + "jobs/flip"] = ""  # written when it is read

        async def snapshot(self) -> dict[str, Any]:
            if self.current == BASE + "jobs/flip":
                read.append("flip")
                title = "Arm Sales Lead" if len(read) == 1 else "Arm Engineer"
                self.pages[self.current] = detail(title, "Arms for robots.")
            return await super().snapshot()

    async def verified(session: finder.FinderSession, path: str, title: str) -> None:
        assert not (await session.navigate("/" + path))[1]
        text, error = await session.report_verified(
            [{"title": title, "url": "/" + path, "text": MODEL_WORDS}]
        )
        assert not error, text
        assert json.loads(text)["kept"] == 1, text

    async def script(session: finder.FinderSession) -> finder.Ran:
        assert session.interactive is not None
        for index, query in enumerate(session.interactive.titles):
            if index:  # back to the results, whose search box and button it then uses
                assert not (await session.navigate("/search"))[1]
            assert not (await session.interact("browser_type", {"ref": "e1", "text": query}))[1]
            assert not (await session.interact("browser_click", {"ref": "e3"}))[1]
            if query == "robot":
                await verified(session, "jobs/robot", "Robot Engineer")
                await verified(session, "jobs/lyon", "Robot Engineer")
                await verified(session, "jobs/cdd", "Robot Engineer CDD")
                await verified(session, "jobs/flip", "Arm Sales Lead")
            else:
                # Read again by the second search, an address is merged, whatever it now says:
                # the unwanted reading of flip stays, the stored robot stays stored once.
                assert await again(session, "jobs/flip", "Arm Engineer") == "duplicate"
                assert await again(session, "jobs/robot") == "duplicate"
        return finder.Ran()

    row = run(factory, new_sweep(factory, alice), runner(script, TwoWays()))
    entry = entry_of(row)
    # Its detail page, then again after the next search (from the results page, round 2).
    assert read == ["flip", "flip"]
    assert (entry["searches"], entry["error"], entry["outcome"]) == (2, None, "complete")
    assert (entry["found"], entry["postings"], row.stats["new"]) == (1, 1, 1)
    assert (entry["unwanted"], entry["other_contract"]) == (2, 1)
    assert [(job.title, job.url) for job in jobs_of(factory, alice)] == [
        ("Robot Engineer", BASE + "jobs/robot")
    ]
    assert_counts_agree(factory, row, alice)
