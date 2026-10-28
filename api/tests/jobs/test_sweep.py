"""Sweeps end to end against a real Postgres, with a fake of the website Runner that stores what
its websites "find" through the run's commits (tests/jobs/sweep_helpers.py): what is stored,
scored and skipped; a website that fails; duplicates and the sources a job names; never_apply;
isolation; crash recovery, cancellation and time limits; the heartbeat and the progress; the
schedules (who is due, a slot started once, concurrency, isolation). Every search runs the ticked
job websites alone (owner, 2026-10-28)."""

import asyncio
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.jobs import sweep
from app.jobs.models import (
    ContractType,
    JobEvent,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
from app.jobs.service import SweepRunningError
from tests.jobs.sweep_helpers import (
    HOST,
    HOST_2,
    SITE,
    SITE_2,
    each_website,
    finding,
    jobs_of,
    new_sweep,
    now,
    read_sweep,
    run,
    run_async,
    search,
    set_profile,
)
from tests.jobs.test_api import seed


def stats(row: Sweep) -> dict:
    return row.stats


def two_websites(**overrides: Any) -> dict[str, Any]:
    return search(interactive_sites=[SITE, SITE_2], **overrides)


AI = finding("AI Engineer", company="Acme AI", published_on="2026-10-01")
APPLIED = finding(
    "Applied AI Engineer, Agents", company="Acme AI", location="London, United Kingdom"
)


# --- the happy path -----------------------------------------------------------------------------


def test_sweep_keeps_the_websites_findings_scored_with_events(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    row = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert row.status is SweepStatus.done
    assert row.finished_at is not None
    assert row.error is None
    s = stats(row)
    # The board counters stay, at zero: the release before this one reads them.
    assert (s["boards_total"], s["boards_done"], s["boards_ok"], s["boards_failed"]) == (
        0, 0, 0, [],
    )  # fmt: skip
    assert (s["seen"], s["matched"], s["new"], s["duplicates"]) == (2, 2, 2, 0)
    assert s["current_board"] is None
    assert s["duration"] >= 0
    [source] = s["sources"]
    assert (source["host"], source["phase"], source["outcome"]) == (HOST, "done", "complete")
    assert (source["found"], source["postings"], source["duplicates"]) == (2, 2, 0)
    jobs = jobs_of(factory, alice)
    assert [(j.company, j.title, j.location) for j in jobs] == [
        ("Acme AI", "AI Engineer", "Paris, France"),
        ("Acme AI", "Applied AI Engineer, Agents", "London, United Kingdom"),
    ]
    first = jobs[0]
    assert first.status is JobStatus.found
    assert first.source == HOST
    assert first.slug == "acme-ai-ai-engineer"
    assert first.contract_type is ContractType.CDI
    assert first.url == AI.url
    assert first.published_on == datetime(2026, 10, 1).date()
    assert jobs[1].published_on is None  # the page stated no date: never today
    assert first.found_on == now().astimezone(sweep.SWEEP_TZ).date()
    assert first.fit is not None
    assert first.fit > 0
    assert first.posting_text == AI.text
    with factory() as db:
        kinds = sorted(
            e.kind.value
            for e in db.execute(sa.select(JobEvent).where(JobEvent.job_id == first.id)).scalars()
        )
        assert kinds == ["created", "fit", "sweep"]
        swept = db.execute(
            sa.select(JobEvent).where(JobEvent.job_id == first.id, JobEvent.kind == "sweep")
        ).scalar_one()
        assert (swept.data["sweep_id"], swept.data["board"]) == (str(row.id), HOST)
        assert swept.data["discovery"]["source_id"] == source["source_id"]
        listed = sweep.new_jobs(db, alice, row.id, 10)
        assert {j.id for j in listed} == {j.id for j in jobs}


def test_a_website_sweep_stores_its_findings_in_batches(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    row = run(
        factory,
        new_sweep(factory, alice),
        finding("AI Engineer"),
        finding("Security Engineer"),
        *[finding(f"GenAI Engineer {n}") for n in range(6)],
    )
    assert row.status is SweepStatus.done, row.error
    assert row.stats["new"] == 8
    assert row.stats["boards_total"] == 0
    assert row.stats["skipped_by_rule"] == 1
    assert len(jobs_of(factory, alice)) == 8


def test_without_skills_jobs_are_stored_unscored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(), skills=None)
    row = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert stats(row)["new"] == 2
    assert all(j.fit is None and j.competencies == [] for j in jobs_of(factory, alice))


def test_second_sweep_finds_nothing_new(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    set_profile(factory, alice, search())
    first = run(factory, new_sweep(factory, alice), AI, APPLIED)
    second = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert stats(first)["new"] == 2
    assert (stats(second)["new"], stats(second)["duplicates"]) == (0, 2)
    assert stats(second)["sources"][0]["outcome"] == "complete"
    assert len(jobs_of(factory, alice)) == 2


def test_unicode_titles_locations_and_text_are_stored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    tokyo = finding(
        "AI Engineer 東京 🤖",
        company="Société Ünïcode",
        location="Paris, Île-de-France",
        text="Rôle à Paris. مرحبا. Python, agents.",
    )
    arabic = finding("مهندس AI Engineer", company="Société Ünïcode")
    row = run(factory, new_sweep(factory, alice), tokyo, arabic)
    assert stats(row)["new"] == 2
    jobs = {j.title: j for j in jobs_of(factory, alice)}
    first = jobs["AI Engineer 東京 🤖"]
    assert first.location == "Paris, Île-de-France"
    assert first.posting_text is not None
    assert "مرحبا" in first.posting_text
    assert first.slug == "societe-unicode-ai-engineer"
    assert jobs["مهندس AI Engineer"].slug == "societe-unicode-ai-engineer-2"


# --- duplicates and the sources a job names -----------------------------------------------------


def test_an_imported_posting_with_a_url_variant_is_a_duplicate(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    seed(factory, alice, [{"company": "Old name", "title": "Something else", "url": AI.url}])
    set_profile(factory, alice, search())
    variant = finding(
        "AI Engineer", company="Acme AI", url=AI.url.replace("https://", "http://www.")
    )
    row = run(factory, new_sweep(factory, alice), variant)
    assert (stats(row)["matched"], stats(row)["new"], stats(row)["duplicates"]) == (1, 0, 1)
    assert stats(row)["sources"][0]["duplicates"] == 1
    assert len(jobs_of(factory, alice)) == 1


def test_same_company_title_location_is_a_duplicate(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    seed(
        factory,
        alice,
        [{"company": "Acme AI", "title": "ai engineer", "location": "PARIS France", "url": None}],
    )
    set_profile(factory, alice, search())
    row = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (1, 1)


def test_an_unnamed_employer_is_a_duplicate_by_its_address_only(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    one = finding("AI Engineer", company=None, url="https://jobs.example-robotics.test/a")
    other = finding("AI Engineer", company=None, url="https://jobs.example-robotics.test/b")
    row = run(factory, new_sweep(factory, alice), one, other, one)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (2, 1)
    assert {j.company for j in jobs_of(factory, alice)} == {sweep.UNKNOWN_EMPLOYER}


def test_the_same_posting_on_two_websites_is_one_row_naming_both(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, two_websites())
    elsewhere = finding("AI Engineer", company="Acme AI", url="https://careers.example-labs.test/1")
    row = run(
        factory,
        new_sweep(factory, alice),
        browser=each_website([AI, APPLIED], [elsewhere]),
    )
    assert (stats(row)["new"], stats(row)["duplicates"]) == (2, 1)
    [job] = [j for j in jobs_of(factory, alice) if j.title == "AI Engineer"]
    assert sweep.noted_sources(job.source) == [HOST, HOST_2]
    # Noting a source is not an edit, and the next sweep notes nothing more.
    again = run(
        factory,
        new_sweep(factory, alice),
        browser=each_website([AI], [elsewhere]),
    )
    assert (stats(again)["new"], stats(again)["duplicates"]) == (0, 2)
    [after] = [j for j in jobs_of(factory, alice) if j.title == "AI Engineer"]
    assert sweep.noted_sources(after.source) == [HOST, HOST_2]
    assert after.updated_at == job.updated_at


def test_a_row_inserted_meanwhile_is_counted_as_duplicate(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A posting added by hand while the sweep runs: the unique URL index stops the insert."""
    set_profile(factory, alice, search())
    real_init = sweep.Known.__init__

    def stale_index(self: sweep.Known, db: Session, user_id: uuid.UUID) -> None:
        real_init(self, db, user_id)
        seed(factory, alice, [{"company": "Z", "title": "Z", "url": AI.url}])

    monkeypatch.setattr(sweep.Known, "__init__", stale_index)
    row = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (1, 1)
    assert row.status is SweepStatus.done


# --- never_apply --------------------------------------------------------------------------------


def test_never_apply_rule_skips_with_the_rule_as_note(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    flagged_finding = finding("AI Engineer, Security Agents", company="Acme AI")
    row = run(factory, new_sweep(factory, alice), flagged_finding, AI)
    assert stats(row)["skipped_by_rule"] == 1
    assert stats(row)["sources"][0]["skipped_by_rule"] == 1
    by_title = {j.title: j for j in jobs_of(factory, alice)}
    flagged = by_title["AI Engineer, Security Agents"]
    assert flagged.status is JobStatus.skipped
    assert flagged.notes == "No security roles: security, cyber (test rule)"
    assert by_title["AI Engineer"].status is JobStatus.found
    with factory() as db:
        kinds = [
            (e.kind.value, e.data.get("via"))
            for e in db.execute(
                sa.select(JobEvent).where(JobEvent.job_id == flagged.id).order_by(JobEvent.id)
            ).scalars()
        ]
    assert ("status", "never_apply") in kinds
    assert ("note", "never_apply") in kinds


@pytest.mark.parametrize(
    ("rule", "terms", "title", "hit"),
    [
        ("No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)",
         ("cyber", "security", "pentest", "threat"), "Threat Intelligence Engineer", True),
        ("No defence: defense; defence.", ("defense", "defence"), "AI Engineer, Defence", True),
        ("No defence roles.", (), "AI Engineer, Defence", False),
        ("Rule: (only a remark)", (), "Anything", False),
        ("No cyber: cyber", ("cyber",), "Cybersecurity Engineer", True),
        ("No banks: Goldman Sachs", ("Goldman Sachs",), "AI Engineer", True),
        ("Odd: a(b, c+", ("a(b", "c+"), "AI Engineer a(b", True),
    ],
)  # fmt: skip
def test_never_apply_rule_parsing(rule: str, terms: tuple, title: str, hit: bool) -> None:
    parsed = sweep.NeverApplyRule.parse(rule)
    assert parsed.terms == terms
    company = "Goldman Sachs" if "Goldman" in rule else "Acme"
    assert parsed.matches(company, title) is hit


# --- a website that fails -----------------------------------------------------------------------


def test_a_website_failure_is_reported_and_the_others_still_run(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, two_websites())
    row = run(
        factory,
        new_sweep(factory, alice),
        browser=each_website([], [AI], errors={0: "navigation_failed"}),
    )
    assert (row.status, row.error) == (SweepStatus.done, None)
    failed, ok = stats(row)["sources"]
    assert (failed["outcome"], failed["reason"]) == ("failed", "navigation_failed")
    assert (ok["outcome"], ok["found"]) == ("complete", 1)
    assert stats(row)["new"] == 1


def test_a_website_that_fails_after_storing_some_is_partial(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    row = run(
        factory,
        new_sweep(factory, alice),
        browser=each_website([AI, APPLIED], errors={0: "page_budget"}),
    )
    assert row.status is SweepStatus.done
    [source] = stats(row)["sources"]
    assert (source["outcome"], source["reason"], source["postings"]) == (
        "partial", "page_budget", 2,
    )  # fmt: skip
    assert len(jobs_of(factory, alice)) == 2


# --- the sweep row ------------------------------------------------------------------------------


def test_no_ticked_website_fails_with_a_clear_message(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(interactive_sites=[{**SITE, "enabled": False}]))
    row = run(factory, new_sweep(factory, alice))
    assert (row.status, row.error) == (SweepStatus.failed, sweep.NO_WEBSITES)
    assert "Find jobs" in sweep.NO_WEBSITES
    set_profile(factory, alice, None)
    row = run(factory, new_sweep(factory, alice))
    assert row.error == sweep.NO_WEBSITES


BROKEN_LIST = {"terms": ["(?P<x>agent)", "(?P<x>llm)"], "advanced_regex": True}


def test_a_search_section_that_no_longer_validates_asks_to_save_it_again(
    factory: sessionmaker[Session], alice: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #11: not "Tick at least one job website" for a section with ticked websites that the
    profile's checks now refuse (stored before they got stricter)."""
    set_profile(factory, alice, search(title_exclude=BROKEN_LIST))
    row = run(factory, new_sweep(factory, alice), AI)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.SEARCH_INVALID)
    assert sweep.SEARCH_INVALID != sweep.NO_WEBSITES
    assert "Search tab" in sweep.SEARCH_INVALID
    assert jobs_of(factory, alice) == []
    [record] = [r for r in caplog.records if "does not validate" in r.getMessage()]
    assert record.user_id == str(alice)
    assert "agent" not in record.getMessage()
    with factory() as db:
        assert sweep.user_search(db, alice) is None  # read-only views: as if there were none
        with pytest.raises(sweep.InvalidSearchError):
            sweep.load_search(db, alice)


def test_a_list_that_stops_compiling_fails_the_sweep_closed(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list that validated but no longer compiles (the regex engine changed meanwhile) keeps
    nothing: the sweep fails with the re-save message instead of searching with a weakened
    exclude list."""
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)

    def broken(terms: list[str], advanced_regex: bool) -> None:
        raise re.error("changed meanwhile")

    monkeypatch.setattr(sweep.matching, "compile_terms", broken)
    row = run(factory, sweep_id, AI)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.SEARCH_INVALID)
    assert jobs_of(factory, alice) == []


def test_a_sweep_picked_twice_is_failed_not_rerun(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    with factory() as db:
        db.get(Sweep, sweep_id).stats = {"picked_at": now().isoformat()}
        db.commit()
    row = run(factory, sweep_id, AI)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.CRASHED)
    assert jobs_of(factory, alice) == []


def test_a_finished_or_missing_sweep_is_left_alone(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    first = run(factory, new_sweep(factory, alice), AI)
    again = run(factory, first.id, APPLIED)
    assert (again.status, again.stats) == (first.status, first.stats)
    assert len(jobs_of(factory, alice)) == 1
    asyncio.run(run_async(factory, uuid.uuid4(), AI))  # nothing happens


def test_time_limit_fails_the_sweep_keeping_what_was_stored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    row = run(
        factory,
        new_sweep(factory, alice),
        timeout_s=0.5,
        browser=each_website([AI, APPLIED], hang_s=10.0),
    )
    assert (row.status, row.error) == (SweepStatus.failed, sweep.TIMED_OUT)
    assert stats(row)["new"] == 2
    [source] = stats(row)["sources"]
    assert (source["outcome"], source["reason"]) == ("partial", "sweep_failed")
    assert len(jobs_of(factory, alice)) == 2


def test_cancellation_marks_the_sweep_interrupted(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)

    async def go() -> None:
        task = asyncio.create_task(
            run_async(factory, sweep_id, browser=each_website([AI], hang_s=10.0))
        )
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)
    assert stats(row)["new"] == 1


def test_an_internal_error_fails_the_sweep(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search())

    def boom(*_: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(sweep, "finish_done", boom)
    row = run(factory, new_sweep(factory, alice), AI)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERNAL)


def test_progress_is_written_while_the_sweep_runs(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    seen: list[dict] = []

    def watch(stop: threading.Event) -> None:
        while not stop.is_set():
            seen.append(dict(read_sweep(factory, sweep_id).stats))
            stop.wait(0.1)

    stop = threading.Event()
    watcher = threading.Thread(target=watch, args=(stop,))
    watcher.start()
    try:
        run(factory, sweep_id, browser=each_website([AI, APPLIED], hang_s=1.0))
    finally:
        stop.set()
        watcher.join()
    running = [s for s in seen if s.get("new") == 2 and s.get("current_board")]
    assert running, seen
    source_id = running[0]["sources"][0]["source_id"]
    assert all(s["current_board"] == source_id for s in running)
    assert all(s["sources"][0]["phase"] == "running" for s in running)
    assert all(s.get("heartbeat_at") for s in running)
    assert read_sweep(factory, sweep_id).stats["current_board"] is None


# --- one running sweep per user, stale ones ------------------------------------------------------


def test_one_running_sweep_per_user_under_concurrency(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def attempt(user_id: uuid.UUID) -> None:
        barrier.wait()
        with factory() as db:
            try:
                sweep.start(db, user_id, SweepTrigger.manual, now())
                db.commit()
                outcomes.append(f"{user_id}:started")
            except SweepRunningError:
                db.rollback()
                outcomes.append(f"{user_id}:running")

    threads = [threading.Thread(target=attempt, args=(u,)) for u in [alice] * 6 + [bob] * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(f"{alice}:started") == 1
    assert outcomes.count(f"{bob}:started") == 1
    assert outcomes.count(f"{alice}:running") == 5


def test_stale_running_sweeps_are_failed(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    long_ago = now() - timedelta(minutes=30)
    with factory() as db:
        picked = Sweep(
            user_id=alice,
            trigger=SweepTrigger.manual,
            started_at=long_ago,
            stats={"picked_at": long_ago.isoformat(), "heartbeat_at": long_ago.isoformat()},
        )
        never_picked = Sweep(user_id=bob, trigger=SweepTrigger.schedule, started_at=long_ago)
        db.add_all([picked, never_picked])
        db.commit()
        alive_id = None
    with factory() as db:
        assert sweep.reap_stale(db, now(), alice) == 1
        db.commit()
    assert read_sweep(factory, picked.id).error == sweep.CRASHED
    assert read_sweep(factory, never_picked.id).status is SweepStatus.running
    with factory() as db:
        alive = sweep.start(db, alice, SweepTrigger.manual, now())  # alice is free again
        db.commit()
        alive_id = alive.id
    with factory() as db:
        assert sweep.reap_stale(db, now()) == 1  # bob's; alice's new one is fresh
        db.commit()
    assert read_sweep(factory, never_picked.id).error == sweep.NOT_PICKED
    assert read_sweep(factory, alive_id).status is SweepStatus.running
    with factory() as db:
        # Waiting in the queue behind other sweeps is not stale: 20 minutes before it fails.
        assert sweep.reap_stale(db, now() + timedelta(minutes=19)) == 0
        assert sweep.reap_stale(db, now() + timedelta(minutes=21)) == 1
        db.commit()


def test_a_picked_sweep_without_heartbeat_for_two_minutes_is_failed(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    beat = now() - timedelta(seconds=90)
    with factory() as db:
        row = Sweep(
            user_id=alice,
            trigger=SweepTrigger.manual,
            started_at=now() - timedelta(minutes=5),
            stats={"picked_at": beat.isoformat(), "heartbeat_at": beat.isoformat()},
        )
        db.add(row)
        db.commit()
        assert sweep.reap_stale(db, now()) == 0
        assert sweep.reap_stale(db, now() + timedelta(seconds=31)) == 1
        db.commit()
    assert read_sweep(factory, row.id).error == sweep.CRASHED


def test_the_heartbeat_moves_while_one_website_is_slow(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    beats: set[str] = set()

    def watch(stop: threading.Event) -> None:
        while not stop.is_set():
            beat = read_sweep(factory, sweep_id).stats.get("heartbeat_at")
            if beat:
                beats.add(beat)
            stop.wait(0.05)

    stop = threading.Event()
    watcher = threading.Thread(target=watch, args=(stop,))
    watcher.start()
    try:
        run(factory, sweep_id, heartbeat_s=0.2, browser=each_website([], hang_s=1.5))
    finally:
        stop.set()
        watcher.join()
    assert len(beats) >= 4


def test_a_heartbeat_keeps_a_long_sweep_alive(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    started = now() - timedelta(hours=1)
    with factory() as db:
        row = Sweep(
            user_id=alice,
            trigger=SweepTrigger.manual,
            started_at=started,
            stats={"picked_at": started.isoformat(), "heartbeat_at": now().isoformat()},
        )
        db.add(row)
        db.commit()
        assert sweep.reap_stale(db, now()) == 0
        row.stats = {"heartbeat_at": "garbage"}
        db.commit()
        assert sweep.reap_stale(db, now()) == 1


# --- isolation ----------------------------------------------------------------------------------


def test_a_sweep_touches_its_user_only(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    # Bob already has the posting; Alice does not: she gets it, his row is untouched.
    [bobs] = seed(factory, bob, [{"title": "AI Engineer", "url": AI.url}])
    set_profile(factory, alice, search())
    set_profile(factory, bob, search())
    row = run(factory, new_sweep(factory, alice), AI, APPLIED)
    assert stats(row)["new"] == 2
    assert [j.id for j in jobs_of(factory, bob)] == [bobs.id]
    with factory() as db:
        assert sweep.new_jobs(db, bob, row.id, 10) == []
        assert (
            db.execute(
                sa.select(sa.func.count()).select_from(JobEvent).where(JobEvent.user_id == bob)
            ).scalar_one()
            == 0
        )


# --- schedules ----------------------------------------------------------------------------------

DAILY = {"enabled": True, "times": ["06:17"], "timezone": "Europe/Paris"}
TICK = datetime(2026, 10, 10, 4, 20, tzinfo=UTC)  # 06:20 in Paris, three minutes after 06:17
SLOT = "2026-10-10T06:17 Europe/Paris"


def due_users(factory: sessionmaker[Session], at: datetime = TICK) -> list[tuple[uuid.UUID, str]]:
    with factory() as db:
        return [(d.user_id, d.slot.key) for d in sweep.scheduled_due(db, at)]


def test_only_enabled_schedules_of_active_users_with_websites_are_due(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    from tests.jobs.conftest import new_user

    carol = new_user(factory, "carol@example.org")
    dave = new_user(factory, "dave@example.org")
    erin = new_user(factory, "erin@example.org")
    frank = new_user(factory, "frank@example.org")
    new_user(factory, "gina@example.org")  # no profile at all
    set_profile(factory, alice, search(sweep_schedule=DAILY))
    set_profile(factory, bob, search())  # default: off
    set_profile(
        factory,
        carol,
        search(interactive_sites=[{**SITE, "enabled": False}], sweep_schedule=DAILY),
    )
    set_profile(factory, dave, search(sweep_schedule=DAILY))
    set_profile(factory, erin, {"days": "not a number"})
    set_profile(
        factory,
        frank,
        search(sweep_schedule={**DAILY, "enabled": False}),
    )
    with factory() as db:
        db.execute(sa.update(User).where(User.id == dave).values(is_active=False))
        db.commit()
    assert due_users(factory) == [(alice, SLOT)]
    # Not every day: Saturday 10 October is not in a weekdays-only schedule.
    weekdays = {**DAILY, "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]}
    set_profile(factory, alice, search(sweep_schedule=weekdays))
    assert due_users(factory) == []


def test_a_scheduled_sweep_of_a_section_that_no_longer_validates_is_logged(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    bob: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #11: skipped as before, but with a clear reason in the worker log, and only for a
    schedule the user turned on."""
    set_profile(factory, alice, search(title_exclude=BROKEN_LIST, sweep_schedule=DAILY))
    set_profile(factory, bob, search(title_exclude=BROKEN_LIST))  # off
    caplog.set_level("WARNING", logger="app.sweep")
    sweep._invalid_logged.clear()

    def logged() -> list[str]:
        return [r.user_id for r in caplog.records if "scheduled sweep skipped" in r.getMessage()]

    assert due_users(factory) == []
    assert logged() == [str(alice)]
    records = [r for r in caplog.records if "scheduled sweep skipped" in r.getMessage()]
    assert "Search tab" in records[0].getMessage()
    assert "agent" not in records[0].getMessage()
    # At most once per user per hour: the next ticks of the hour stay quiet.
    for minutes in (5, 30, 59):
        assert due_users(factory, TICK + timedelta(minutes=minutes)) == []
    assert logged() == [str(alice)]
    assert due_users(factory, TICK + timedelta(minutes=60)) == []
    assert logged() == [str(alice)] * 2


def test_a_section_without_a_schedule_or_with_the_old_flag_is_off(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    old = search()
    old.pop("sweep_schedule", None)
    set_profile(factory, alice, old)
    set_profile(factory, bob, {**search(), "sweep_nightly": True})
    assert due_users(factory) == []
    with factory() as db:
        assert sweep.user_search(db, bob) is not None  # the old key is dropped, not refused


def test_a_slot_starts_once_whatever_the_ticks(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(sweep_schedule=DAILY))
    [due] = sweep_due(factory)
    with factory() as db:
        row = sweep.start_scheduled(db, due, TICK)
        db.commit()
    assert row is not None
    assert (row.trigger, row.stats) == (SweepTrigger.schedule, {**row.stats, "schedule_slot": SLOT})
    # Running: the next ticks of the window find the slot taken.
    assert due_users(factory, TICK + timedelta(minutes=5)) == []
    with factory() as db:
        assert sweep.start_scheduled(db, due, TICK) is None
        db.rollback()
    # Run it: the slot stays recorded through the claim, the progress and the end. It searches
    # the ticked website (owner, 2026-10-28).
    searched: list[str] = []

    async def browser(plan: Any, *args: Any) -> None:
        searched.extend(site.url for site in plan.search.interactive_sites)

    finished = run(factory, row.id, browser=browser)
    assert finished.status is SweepStatus.done
    assert finished.stats["schedule_slot"] == SLOT
    assert (finished.stats["boards_total"], finished.stats["new"]) == (0, 0)
    assert searched == [SITE["url"]]
    assert due_users(factory, TICK + timedelta(minutes=10)) == []
    # The next day's slot is a new one.
    assert due_users(factory, TICK + timedelta(days=1)) == [
        (alice, "2026-10-11T06:17 Europe/Paris")
    ]


def sweep_due(factory: sessionmaker[Session], at: datetime = TICK) -> list[sweep.DueSweep]:
    with factory() as db:
        return sweep.scheduled_due(db, at)


def test_a_user_already_sweeping_is_skipped_and_retried_within_the_window(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(sweep_schedule=DAILY))
    with factory() as db:
        manual = sweep.start(db, alice, SweepTrigger.manual, TICK).id
        db.commit()
    [due] = sweep_due(factory)
    with factory() as db, pytest.raises(SweepRunningError):
        sweep.start_scheduled(db, due, TICK)
    with factory() as db:
        row = db.get(Sweep, manual)
        row.status, row.finished_at = SweepStatus.done, now()
        db.commit()
    later = TICK + timedelta(minutes=5)
    [again] = sweep_due(factory, later)
    assert again.slot.key == SLOT
    with factory() as db:
        assert sweep.start_scheduled(db, again, later) is not None
        db.commit()


def test_concurrent_ticks_start_a_slot_once(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(sweep_schedule=DAILY))
    [due] = sweep_due(factory)
    outcomes: list[str] = []
    barrier = threading.Barrier(6)

    def tick() -> None:
        barrier.wait()
        with factory() as db:
            try:
                row = sweep.start_scheduled(db, due, TICK)
                db.commit()
                outcomes.append("started" if row else "taken")
            except SweepRunningError:
                db.rollback()
                outcomes.append("running")

    threads = [threading.Thread(target=tick) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("started") == 1
    with factory() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(Sweep)).scalar_one() == 1


def test_schedules_are_per_user(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search(sweep_schedule=DAILY))
    set_profile(
        factory,
        bob,
        search(
            sweep_schedule={**DAILY, "times": ["06:17"], "timezone": "UTC"},
        ),
    )
    assert due_users(factory) == [(alice, SLOT)]
    [due] = sweep_due(factory)
    with factory() as db:
        sweep.start_scheduled(db, due, TICK)
        db.commit()
    # Bob's 06:17 UTC slot comes two hours later and is his alone; Alice's sweep is not his.
    bob_tick = datetime(2026, 10, 10, 6, 20, tzinfo=UTC)
    assert due_users(factory, bob_tick) == [(bob, "2026-10-10T06:17 UTC")]
    with factory() as db:
        assert not sweep.slot_taken(db, bob, SLOT)
        assert sweep.slot_taken(db, alice, SLOT)
