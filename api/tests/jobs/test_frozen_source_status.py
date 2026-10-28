"""Every frozen source (a ticked job website) has one durable identity throughout the sweep
lifecycle: start, reopened views, progress, and every end."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.jobs import sweep
from app.jobs.models import Sweep
from app.jobs.sweep_router import sweep_out
from app.profile import schemas
from app.profile import service as profile_service
from tests.browser.fakes import FakeTools
from tests.jobs.sweep_helpers import (
    each_website,
    finding,
    read_sweep,
    search,
    set_profile,
    website_runner,
)
from tests.jobs.test_frozen_search_scope import CANARY, alice, fake_queue, site

__all__ = ["alice", "fake_queue"]

SECOND, OFF = "https://second.example.org/jobs", "https://off.example.org/"
BEFORE, AFTER = "https://before.example.org/", "https://after.example.org/"


def start(factory, alice, clock, queue):
    """The start of POST /api/jobs/sweeps, through the shared `begin`."""
    return asyncio.run(sweep.begin(factory, queue, alice.id, clock(), sweep_out))


def two_websites(**overrides):
    return search(
        browser_sources=[
            {
                "kind": "linkedin", "query": "AI", "enabled": True,
                "accepted_at": "2026-10-08T09:30:00+00:00", "text_version": 1,
            },
        ],  # a consent row: never a source of its own
        interactive_sites=[site(), site(url=SECOND), site(url=OFF, enabled=False)],
        interactive_instructions=CANARY,
        **overrides,
    )  # fmt: skip


@pytest.mark.parametrize("ending", ["done", "fail", "reap", "crash", "queue"])
def test_all_selected_sources_survive_start_reopened_and_terminal_views(
    ending, factory, clock, api, alice, fake_queue
):
    set_profile(factory, alice.id, two_websites())
    fake_queue.down = ending == "queue"
    begun = start(factory, alice, clock, fake_queue)
    assert begun.outcome == ("queue_down" if ending == "queue" else "started")
    with factory() as db:
        row = db.query(Sweep).filter_by(user_id=alice.id).one()
        sweep_id = row.id
    body = sweep_out(row).model_dump(mode="json")
    assert body["search_scope"]["source_count"] == 2
    entries = body["sources"]
    assert len(entries) == 2
    expected = {(entry["source_id"], entry["mode"]) for entry in entries}
    assert len(expected) == 2
    assert {mode for _, mode in expected} == {"llm_browser"}
    assert [entry["host"] for entry in entries] == ["jobs.example.org", "second.example.org"]
    if ending != "queue":
        assert begun.view.model_dump(mode="json")["sources"] == entries
        assert {entry["phase"] for entry in entries} == {"pending"}
        for path in ("/api/jobs/sweeps", f"/api/jobs/sweeps/{sweep_id}"):
            before_work = api.request("GET", path, token=alice.token)
            assert before_work.status_code == 200
            assert CANARY not in before_work.text
            current = before_work.json()
            assert (current["items"][0] if "items" in current else current)["sources"] == entries
        assert sweep.claim(factory, sweep_id, clock()) is not None
        progress = sweep.Stats(new=3, picked_at=clock().isoformat())
        sweep.save_progress(factory, sweep_id, progress, clock())
        if ending == "done":
            sweep.finish_done(factory, sweep_id, sweep.Stats(new=3), clock())
        elif ending == "fail":
            sweep.fail_sweep(factory, sweep_id, sweep.INTERNAL, clock(), sweep.Stats(new=3))
        elif ending == "crash":
            assert sweep.claim(factory, sweep_id, clock()) is None
        else:
            with factory() as db:
                assert sweep.reap_stale(db, clock() + timedelta(hours=1), alice.id) == 1
                db.commit()
    for path in ("/api/jobs/sweeps", f"/api/jobs/sweeps/{sweep_id}"):
        reopened = api.request("GET", path, token=alice.token)
        assert reopened.status_code == 200
        assert CANARY not in reopened.text
        out = reopened.json()
        actual = out["items"][0] if "items" in out else out
        assert {(entry["source_id"], entry["mode"]) for entry in actual["sources"]} == expected
        assert len(actual["sources"]) == 2
        assert {entry["phase"] for entry in actual["sources"]} == {"done"}
    events = api.request("GET", f"/api/jobs/sweeps/{sweep_id}/events", token=alice.token)
    assert CANARY not in events.text
    for source_id, _ in expected:
        assert source_id in events.text


def test_colon_neighbors_have_distinct_stable_source_identities(factory, clock, alice):
    selected = search(
        interactive_sites=[
            site(url="https://careers.example.org/a:b"),
            site(url="https://careers.example.org/a"),
        ]
    )
    set_profile(factory, alice.id, selected)
    begun = sweep._insert(factory, alice.id, clock(), sweep_out)
    assert begun.outcome == "started"
    first = read_sweep(factory, begun.sweep_id).stats["search_scope"]["sources"]
    assert len({entry["source_id"] for entry in first}) == 2
    sweep.finish_done(factory, begun.sweep_id, sweep.Stats(), clock())
    again = sweep._insert(factory, alice.id, clock(), sweep_out)
    assert read_sweep(factory, again.sweep_id).stats["search_scope"]["sources"] == first


@pytest.mark.parametrize("runner", ["fake", "real"])
def test_website_updates_bind_to_frozen_entries_without_duplicate_or_counter_loss(
    factory, clock, api, alice, fake_queue, monkeypatch, runner
):
    set_profile(factory, alice.id, two_websites())
    sweep_id = start(factory, alice, clock, fake_queue).sweep_id
    initial = read_sweep(factory, sweep_id).stats["sources"]
    snapshots = []
    original = sweep.save_progress

    def record(sessions, row_id, stats, now):
        original(sessions, row_id, stats, now)
        snapshots.append(copy.deepcopy(read_sweep(factory, row_id).stats["sources"]))

    monkeypatch.setattr(sweep, "save_progress", record)
    browser = (
        each_website([finding("AI Engineer")], [])
        if runner == "fake"
        else website_runner(FakeTools({}))
    )
    asyncio.run(sweep.run_sweep(factory, sweep_id, clock, browser=browser))
    row = read_sweep(factory, sweep_id)
    assert sweep._stats_json(row, sweep.Stats())["sources"] == row.stats["sources"]
    expected_ids = [entry["source_id"] for entry in initial]
    for entries in [*snapshots, row.stats["sources"]]:
        assert [entry["source_id"] for entry in entries] == expected_ids
        assert all(entry["mode"] == "llm_browser" for entry in entries)
    first, second = row.stats["sources"]
    assert all(entry["phase"] == "done" for entry in (first, second))
    if runner == "fake":
        assert first["postings"] == first["found"] == row.stats["new"] == 1
        assert (first["outcome"], second["outcome"]) == ("complete", "no_matches")
        assert any(any(entry["phase"] == "running" for entry in entries) for entries in snapshots)
    else:
        # No model access in this run: each website says so, and no page is opened.
        assert first["reason"] == "no_credential"
        assert row.stats["new"] == 0


def test_held_edit_wins_before_start_and_returns_revision_conflict(factory, clock, alice):
    set_profile(factory, alice.id, search(interactive_sites=[site(url=BEFORE)]))
    attempted = threading.Event()
    main_thread = threading.get_ident()
    engine = factory.kw["bind"]

    def observe(conn, cursor, statement, parameters, context, executemany):
        if threading.get_ident() != main_thread and "FOR UPDATE" in statement:
            attempted.set()

    sa.event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, factory() as editor:
            profile_service.save_section(
                editor,
                alice.id,
                "search",
                schemas.validate_section("search", search(interactive_sites=[site(url=AFTER)])),
                1,
                clock(),
            )
            pending = pool.submit(sweep._insert, factory, alice.id, clock(), sweep_out, 1)
            try:
                assert attempted.wait(30)
            finally:
                editor.commit()
            assert pending.result(timeout=30).outcome == "revision_conflict"
        with factory() as db:
            assert db.query(Sweep).filter_by(user_id=alice.id).count() == 0
    finally:
        sa.event.remove(engine, "before_cursor_execute", observe)


def test_held_start_wins_and_later_edit_cannot_change_frozen_scope(factory, clock, alice):
    set_profile(factory, alice.id, search(interactive_sites=[site(url=BEFORE)]))
    viewed, release, edit_attempted = threading.Event(), threading.Event(), threading.Event()
    engine = factory.kw["bind"]

    def view(row):
        viewed.set()
        assert release.wait(30)
        return row.id

    def edit():
        with factory() as db:
            db.connection(execution_options={"j629_editor": True})
            profile_service.save_section(
                db,
                alice.id,
                "search",
                schemas.validate_section("search", search(interactive_sites=[site(url=AFTER)])),
                1,
                clock(),
            )
            db.commit()

    def observe(conn, cursor, statement, parameters, context, executemany):
        if context.execution_options.get("j629_editor") and "FOR UPDATE" in statement:
            edit_attempted.set()

    sa.event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            started = pool.submit(sweep._insert, factory, alice.id, clock(), view, 1)
            try:
                assert viewed.wait(30)
                edited = pool.submit(edit)
                assert edit_attempted.wait(30)
            finally:
                release.set()
            begun = started.result(timeout=30)
            edited.result(timeout=30)
        assert begun.outcome == "started"
        row = read_sweep(factory, begun.sweep_id)
        assert row.stats["search_scope"]["profile_revision"] == 1
        frozen = row.stats["search_scope"]["search"]["interactive_sites"]
        assert [entry["url"] for entry in frozen] == [BEFORE]
        claimed = sweep.claim(factory, begun.sweep_id, clock())
        assert [entry.url for entry in claimed.search.interactive_sites] == [BEFORE]
    finally:
        sa.event.remove(engine, "before_cursor_execute", observe)
