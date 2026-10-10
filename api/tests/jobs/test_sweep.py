"""Sweeps end to end against a real Postgres and the fake ATS server over real HTTP
(tests/jobs/fake_ats.py): what is kept, stored, scored and skipped; every failure mode of a
board; duplicates; never_apply; isolation; crash recovery, cancellation and time limits; the
schedules (who is due, a slot started once, concurrency, isolation); and the memory a 5,000
posting board costs."""

import asyncio
import gc
import re
import threading
import time
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.jobs import sweep
from app.jobs.models import (
    ContractType,
    Job,
    JobEvent,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
from app.jobs.service import SweepRunningError
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import (
    board,
    fake_ats,
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

__all__ = ["fake_ats"]


def stats(row: Sweep) -> dict:
    return row.stats


# --- the happy path -----------------------------------------------------------------------------


def test_sweep_keeps_new_matching_postings_scored_with_events(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok", "ashby", "Acme AI")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert row.status is SweepStatus.done
    assert row.finished_at is not None
    assert row.error is None
    s = stats(row)
    assert (s["boards_total"], s["boards_done"], s["boards_ok"], s["boards_failed"]) == (
        1, 1, 1, [],
    )  # fmt: skip
    assert (s["seen"], s["matched"], s["new"], s["duplicates"]) == (3, 2, 2, 0)
    assert s["current_board"] is None
    assert s["duration"] >= 0
    jobs = jobs_of(factory, alice)
    assert [(j.company, j.title, j.location) for j in jobs] == [
        ("Acme AI", "AI Engineer", "Paris, France"),
        ("Acme AI", "Applied AI Engineer, Agents", "London, United Kingdom"),
    ]
    first = jobs[0]
    assert first.status is JobStatus.found
    assert first.source == "ashby:fake-ok"
    assert first.slug == "acme-ai-ai-engineer"
    assert first.contract_type is ContractType.CDI
    assert first.url == "https://jobs.example.org/fake-ok/1"
    assert first.published_on is not None
    assert first.found_on == now().astimezone(sweep.SWEEP_TZ).date()
    assert first.fit is not None
    assert first.fit > 0
    assert "agents" in first.competencies
    assert "python" in first.competencies
    with factory() as db:
        kinds = sorted(
            e.kind.value
            for e in db.execute(sa.select(JobEvent).where(JobEvent.job_id == first.id)).scalars()
        )
        assert kinds == ["created", "fit", "sweep"]
        swept = db.execute(
            sa.select(JobEvent).where(JobEvent.job_id == first.id, JobEvent.kind == "sweep")
        ).scalar_one()
        assert swept.data == {"sweep_id": str(row.id), "board": "ashby:fake-ok"}
        listed = sweep.new_jobs(db, alice, row.id, 10)
        assert {j.id for j in listed} == {j.id for j in jobs}


def test_each_ats_parsed_through_http(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(
        factory,
        alice,
        search(
            board("fake-ok", "ashby", "A"),
            board("fake-ok", "greenhouse", "G"),
            board("fake-ok", "lever", "L"),
        ),
    )
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(row)["boards_ok"] == 3
    # Same URLs on the three boards: the first stored wins, the others are duplicates.
    assert stats(row)["new"] == 2
    assert stats(row)["duplicates"] == 4
    texts = {j.posting_text for j in jobs_of(factory, alice)}
    assert "Requirements: Python, agents, LLM evaluation." in texts


def test_without_skills_jobs_are_stored_unscored(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok")), skills=None)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(row)["new"] == 2
    assert all(j.fit is None and j.competencies == [] for j in jobs_of(factory, alice))


def test_second_sweep_finds_nothing_new(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), board("fake-ok2", "lever")))
    first = run(factory, new_sweep(factory, alice), fake_ats)
    second = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(first)["new"] == 3
    assert (stats(second)["new"], stats(second)["duplicates"]) == (0, 3)
    assert len(jobs_of(factory, alice)) == 3


# --- filters ------------------------------------------------------------------------------------


def test_days_window_board_exclude_and_contract_types(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    excluded = board(
        "fake-ok", "lever", extra_exclude={"terms": ["applied"], "advanced_regex": False}
    )
    set_profile(factory, alice, search(excluded, board("fake-ok2", "greenhouse")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    titles = sorted(j.title for j in jobs_of(factory, alice))
    # "Applied AI Engineer" excluded by the board, "GenAI Engineer" published 40 days ago.
    assert titles == ["AI Engineer", "Forward Deployed Engineer"]
    assert (stats(row)["seen"], stats(row)["matched"]) == (5, 2)

    set_profile(factory, alice, search(board("fake-unicode"), contract_types=["VIE"]))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(row)["matched"] == 0


def test_unicode_titles_locations_and_text_are_stored(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-unicode", "greenhouse", "Société Ünïcode")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(row)["new"] == 2
    jobs = {j.title: j for j in jobs_of(factory, alice)}
    first = jobs["AI Engineer 東京 🤖"]
    assert first.location == "Paris, Île-de-France"
    assert first.posting_text is not None
    assert "\x00" not in first.posting_text
    assert "مرحبا" in first.posting_text
    assert first.slug == "societe-unicode-ai-engineer"
    assert jobs["مهندس AI Engineer"].slug == "societe-unicode-ai-engineer-2"


# --- duplicates ---------------------------------------------------------------------------------


def test_an_imported_posting_with_a_url_variant_is_a_duplicate(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    seed(
        factory,
        alice,
        [
            {
                "company": "Old name",
                "title": "Something else",
                "url": "https://jobs.example.org/fake-ok/1",
            }
        ],
    )
    set_profile(factory, alice, search(board("fake-variant")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (stats(row)["matched"], stats(row)["new"], stats(row)["duplicates"]) == (1, 0, 1)
    assert len(jobs_of(factory, alice)) == 1


def test_same_company_title_location_is_a_duplicate(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    seed(
        factory,
        alice,
        [{"company": "Ok", "title": "ai engineer", "location": "PARIS France", "url": None}],
    )
    set_profile(factory, alice, search(board("fake-ok")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (1, 1)


def test_duplicate_postings_across_boards_stored_once(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), board("fake-dup")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (2, 2)


def test_a_row_inserted_meanwhile_is_counted_as_duplicate(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A posting added by hand while the sweep runs: the unique URL index stops the insert."""
    set_profile(factory, alice, search(board("fake-ok")))
    real_init = sweep.Known.__init__

    def stale_index(self: sweep.Known, db: Session, user_id: uuid.UUID) -> None:
        real_init(self, db, user_id)
        seed(
            factory,
            alice,
            [{"company": "Z", "title": "Z", "url": "https://jobs.example.org/fake-ok/1"}],
        )

    monkeypatch.setattr(sweep.Known, "__init__", stale_index)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (stats(row)["new"], stats(row)["duplicates"]) == (1, 1)
    assert row.status is SweepStatus.done


# --- never_apply --------------------------------------------------------------------------------


def test_never_apply_rule_skips_with_the_rule_as_note(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-security"), board("fake-ok")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert stats(row)["skipped_by_rule"] == 1
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
        ("No cyber: cyber", ("cyber",), "Cybersecurity Engineer", False),
        ("No banks: Goldman Sachs", ("Goldman Sachs",), "AI Engineer", True),
        ("Odd: a(b, c+", ("a(b", "c+"), "AI Engineer a(b", True),
    ],
)  # fmt: skip
def test_never_apply_rule_parsing(rule: str, terms: tuple, title: str, hit: bool) -> None:
    parsed = sweep.NeverApplyRule.parse(rule)
    assert parsed.terms == terms
    company = "Goldman Sachs" if "Goldman" in rule else "Acme"
    assert parsed.matches(company, title) is hit


# --- failures of boards -------------------------------------------------------------------------


def test_every_failure_mode_is_reported_and_the_others_still_run(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    boards = [
        board("fake-ok", "ashby", "Good"),
        board("fake-404", "ashby", "Missing"),
        board("fake-403", "greenhouse", "Refused"),
        board("fake-429", "lever", "Busy once"),
        board("fake-429-always", "lever", "Busy"),
        board("fake-500", "greenhouse", "Broken"),
        board("fake-503-once", "ashby", "Flaky"),
        board("fake-timeout", "ashby", "Slow"),
        board("fake-malformed", "greenhouse", "Garbled"),
        board("fake-shape", "ashby", "Odd"),
        board("fake-redirect", "lever", "Moved"),
        board("fake-redirect-away", "lever", "Away"),
    ]
    set_profile(factory, alice, search(*boards))
    row = run(factory, new_sweep(factory, alice), fake_ats, client_options={"timeout_s": 0.5})
    s = stats(row)
    assert row.status is SweepStatus.done
    failed = {f["company"]: f["error"] for f in s["boards_failed"]}
    assert failed == {
        "Missing": "not_found",
        "Refused": "blocked",
        "Busy": "rate_limited",
        "Broken": "upstream_error",
        "Slow": "timeout",
        "Garbled": "invalid_response",
        "Odd": "invalid_response",
        "Away": "upstream_error",
    }
    assert all(f["message"] and f["org"] and f["ats"] for f in s["boards_failed"])
    assert (s["boards_total"], s["boards_done"], s["boards_ok"]) == (12, 12, 4)
    # Good, Busy once, Flaky and Moved all serve fake-ok's postings: stored once.
    assert (s["new"], s["duplicates"]) == (2, 6)


def test_all_boards_failing_is_still_a_finished_sweep(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-404"), board("fake-500", "lever")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert row.status is SweepStatus.done
    assert stats(row)["boards_ok"] == 0
    assert len(stats(row)["boards_failed"]) == 2


def test_a_board_that_cannot_be_stored_fails_alone(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_profile(
        factory,
        alice,
        search(board("fake-ok", company="First"), board("fake-ok2", "lever", "Second")),
    )
    real = sweep.store_board

    def broken(db, plan, b, result, stats_, when):  # type: ignore[no-untyped-def]
        real(db, plan, b, result, stats_, when)
        if b.company == "First":
            raise RuntimeError("disk full")

    monkeypatch.setattr(sweep, "store_board", broken)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    s = stats(row)
    assert [f["error"] for f in s["boards_failed"]] == ["store_failed"]
    assert {j.company for j in jobs_of(factory, alice)} == {"Second"}
    assert (s["seen"], s["new"]) == (2, 1)  # the failed board's counts were put back


# --- the sweep row ------------------------------------------------------------------------------


def test_no_enabled_board_fails_with_a_clear_message(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search({**board("fake-ok"), "enabled": False}))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.NO_BOARDS)
    set_profile(factory, alice, None)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert row.error == sweep.NO_BOARDS


BROKEN_LIST = {"terms": ["(?P<x>agent)", "(?P<x>llm)"], "advanced_regex": True}


def test_a_search_section_that_no_longer_validates_asks_to_save_it_again(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #11: not "No board is enabled" for a section with enabled boards that the profile's
    checks now refuse (stored before they got stricter)."""
    set_profile(factory, alice, search(board("fake-ok"), title_exclude=BROKEN_LIST))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.SEARCH_INVALID)
    assert sweep.SEARCH_INVALID != sweep.NO_BOARDS
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
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list that validated but no longer compiles (the regex engine changed meanwhile) keeps
    nothing: the sweep fails with the re-save message instead of dropping excluded terms."""
    set_profile(factory, alice, search(board("fake-ok")))

    def broken(terms: list[str], advanced_regex: bool) -> None:
        raise re.error("changed meanwhile")

    monkeypatch.setattr(sweep.matching, "compile_terms", broken)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.SEARCH_INVALID)
    assert jobs_of(factory, alice) == []


def test_fetched_boards_in_memory_never_exceed_the_concurrency(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #11: a board holds its slot from its fetch until it is stored, so with a slow store
    step the fetched boards waiting for it never pile up past `concurrency`."""
    orgs = ["fake-ok", "fake-ok2", "fake-dup", "fake-unicode", "fake-variant", "fake-security"]
    boards = [board(org, name, f"Co {name} {org}") for name in ("ashby", "lever") for org in orgs]
    set_profile(factory, alice, search(*boards))
    in_memory = 0
    peak = 0
    guard = threading.Lock()
    real_fetch = sweep.ats.AtsClient.fetch_board
    real_store = sweep._store

    async def counting_fetch(self, ats_name, org):  # type: ignore[no-untyped-def]
        nonlocal in_memory, peak
        with guard:
            in_memory += 1
            peak = max(peak, in_memory)
        return await real_fetch(self, ats_name, org)

    def slow_store(*args):  # type: ignore[no-untyped-def]
        nonlocal in_memory
        time.sleep(0.05)  # fetches finish faster than boards are stored
        try:
            return real_store(*args)
        finally:
            with guard:
                in_memory -= 1

    monkeypatch.setattr(sweep.ats.AtsClient, "fetch_board", counting_fetch)
    monkeypatch.setattr(sweep, "_store", slow_store)
    row = run(factory, new_sweep(factory, alice), fake_ats, concurrency=2)
    assert row.status is SweepStatus.done
    assert row.stats["boards_done"] == len(boards)
    assert row.stats["boards_ok"] == len(boards)
    assert peak == 2, peak
    assert in_memory == 0


def test_without_an_org_prefix_every_org_goes_to_the_stub_and_real_ones_are_not_found(
    fake_ats: FakeAts,
) -> None:
    """The CI stack (tests/ats-stub.compose.yml) sets ATS_BASE_URL with no org prefix: a real org
    named by any spec reaches the fake server and answers "board not found", never the real ATS."""

    async def go() -> list[str]:
        codes = []
        async with sweep.ats.AtsClient(tz=sweep.SWEEP_TZ, stub_base_url=fake_ats.url) as c:
            for ats_name, org in (
                ("ashby", "mistral.ai"),
                ("greenhouse", "anthropic"),
                ("lever", "x"),
            ):
                with pytest.raises(sweep.ats.BoardError) as caught:
                    await c.fetch_board(ats_name, org)
                codes.append(caught.value.code)
            assert len((await c.fetch_board("ashby", "fake-ok")).postings) == 3
        return codes

    assert asyncio.run(go()) == ["not_found"] * 3
    assert fake_ats.state.counts == {
        "ashby/mistral.ai": 1,
        "greenhouse/anthropic": 1,
        "lever/x": 1,
        "ashby/fake-ok": 1,
    }


def test_a_failed_board_gives_its_slot_back(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    """Failed fetches release their slot too: one slot, five boards failing, the sweep ends."""
    gone = [board(org, name) for name in ("ashby", "lever") for org in ("fake-404", "fake-500")]
    set_profile(factory, alice, search(*gone, board("fake-ok")))
    row = run(factory, new_sweep(factory, alice), fake_ats, concurrency=1)
    assert row.status is SweepStatus.done
    assert (row.stats["boards_done"], len(row.stats["boards_failed"])) == (5, 4)
    assert row.stats["new"] == 2


def test_a_sweep_picked_twice_is_failed_not_rerun(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok")))
    sweep_id = new_sweep(factory, alice)
    with factory() as db:
        db.get(Sweep, sweep_id).stats = {"picked_at": now().isoformat()}
        db.commit()
    row = run(factory, sweep_id, fake_ats)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.CRASHED)
    assert jobs_of(factory, alice) == []


def test_a_finished_or_missing_sweep_is_left_alone(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok")))
    first = run(factory, new_sweep(factory, alice), fake_ats)
    again = run(factory, first.id, fake_ats)
    assert (again.status, again.stats) == (first.status, first.stats)
    asyncio.run(run_async(factory, uuid.uuid4(), fake_ats))  # nothing happens


def test_time_limit_fails_the_sweep_keeping_what_was_stored(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), board("fake-timeout")))
    row = run(
        factory,
        new_sweep(factory, alice),
        fake_ats,
        timeout_s=1.0,
        client_options={"timeout_s": 5.0},
    )
    assert (row.status, row.error) == (SweepStatus.failed, sweep.TIMED_OUT)
    assert stats(row)["new"] == 2
    assert len(jobs_of(factory, alice)) == 2


def test_cancellation_marks_the_sweep_interrupted(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-timeout")))
    sweep_id = new_sweep(factory, alice)

    async def go() -> None:
        task = asyncio.create_task(
            run_async(factory, sweep_id, fake_ats, client_options={"timeout_s": 10.0})
        )
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)


def test_an_internal_error_fails_the_sweep(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_profile(factory, alice, search(board("fake-ok")))

    def boom(*_: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(sweep, "finish_done", boom)
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERNAL)


def test_progress_is_written_while_the_sweep_runs(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), board("fake-timeout", company="Slow one")))
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
        run(factory, sweep_id, fake_ats, client_options={"timeout_s": 5.0})
    finally:
        stop.set()
        watcher.join()
    running = [s for s in seen if s.get("boards_done") == 1]
    assert running, seen
    assert any(s.get("current_board") in ("Ok", "Slow one") for s in seen)
    assert all(s.get("heartbeat_at") for s in running)


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


def test_the_heartbeat_moves_while_one_board_is_slow(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-timeout")))
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
        run(
            factory,
            sweep_id,
            fake_ats,
            heartbeat_s=0.2,
            client_options={"timeout_s": 2.0, "attempts": 1},
        )
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
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID, fake_ats: FakeAts
) -> None:
    # Bob already has the posting; Alice does not: she gets it, his row is untouched.
    [bobs] = seed(
        factory, bob, [{"title": "AI Engineer", "url": "https://jobs.example.org/fake-ok/1"}]
    )
    set_profile(factory, alice, search(board("fake-ok")))
    set_profile(factory, bob, search(board("fake-ok2")))
    row = run(factory, new_sweep(factory, alice), fake_ats)
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


def test_only_enabled_schedules_of_active_users_with_boards_are_due(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    from tests.jobs.conftest import new_user

    carol = new_user(factory, "carol@example.org")
    dave = new_user(factory, "dave@example.org")
    erin = new_user(factory, "erin@example.org")
    frank = new_user(factory, "frank@example.org")
    new_user(factory, "gina@example.org")  # no profile at all
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
    set_profile(factory, bob, search(board("fake-ok")))  # the default: off
    set_profile(
        factory, carol, search({**board("fake-ok"), "enabled": False}, sweep_schedule=DAILY)
    )
    set_profile(factory, dave, search(board("fake-ok"), sweep_schedule=DAILY))
    set_profile(factory, erin, {"days": "not a number"})
    set_profile(
        factory, frank, search(board("fake-ok"), sweep_schedule={**DAILY, "enabled": False})
    )
    with factory() as db:
        db.execute(sa.update(User).where(User.id == dave).values(is_active=False))
        db.commit()
    assert due_users(factory) == [(alice, SLOT)]
    # Not every day: Saturday 10 October is not in a weekdays-only schedule.
    weekdays = {**DAILY, "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]}
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=weekdays))
    assert due_users(factory) == []


def test_a_scheduled_sweep_of_a_section_that_no_longer_validates_is_logged(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    bob: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #11: skipped as before, but with a clear reason in the worker log, and only for a
    schedule the user turned on."""
    set_profile(
        factory, alice, search(board("fake-ok"), title_exclude=BROKEN_LIST, sweep_schedule=DAILY)
    )
    set_profile(factory, bob, search(board("fake-ok"), title_exclude=BROKEN_LIST))  # off
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
    old = search(board("fake-ok"))
    old.pop("sweep_schedule", None)
    set_profile(factory, alice, old)
    set_profile(factory, bob, {**search(board("fake-ok")), "sweep_nightly": True})
    assert due_users(factory) == []
    with factory() as db:
        assert sweep.user_search(db, bob) is not None  # the old key is dropped, not refused


def test_a_slot_starts_once_whatever_the_ticks(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
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
    # Run it: the slot stays recorded through the claim, the progress and the end.
    finished = run(factory, row.id, fake_ats)
    assert finished.status is SweepStatus.done
    assert finished.stats["schedule_slot"] == SLOT
    assert finished.stats["new"] == 2
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
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
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
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
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
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
    set_profile(
        factory,
        bob,
        search(board("fake-ok"), sweep_schedule={**DAILY, "times": ["06:17"], "timezone": "UTC"}),
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


# --- memory -------------------------------------------------------------------------------------


def test_a_5000_posting_board_is_swept_within_memory_and_time(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(
        factory,
        alice,
        search(board("fake-big", "ashby", "Big"), board("fake-big", "greenhouse", "Big GH")),
    )
    sweep_id = new_sweep(factory, alice)
    gc.collect()
    tracemalloc.start()
    started = datetime.now(UTC)
    try:
        row = run(factory, sweep_id, fake_ats, client_options={"timeout_s": 30.0})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed = (datetime.now(UTC) - started).total_seconds()
    s = stats(row)
    assert row.status is SweepStatus.done, row.error
    assert (s["seen"], s["new"], s["duplicates"]) == (10_000, 5_000, 5_000)
    with factory() as db:
        count = db.execute(
            sa.select(sa.func.count()).select_from(Job).where(Job.user_id == alice)
        ).scalar_one()
    assert count == 5_000
    mib = peak / 2**20
    print(f"\n5,000 postings x 2 boards: peak Python allocations {mib:.0f} MiB, {elapsed:.1f}s")
    assert peak < 400 * 2**20
    assert elapsed < 300
