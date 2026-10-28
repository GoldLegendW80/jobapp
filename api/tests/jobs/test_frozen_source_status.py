"""Every frozen source has one durable identity throughout the sweep lifecycle.

Every search runs the websites alone since the owner's decision of 2026-10-28; these tests
start the sweep engine with every kind of source (`websites_only=False`, its internal start),
so the identities of boards, feeds and readers stay pinned while the engine keeps them."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa
from app.opportunities.sources import FeedClient, FeedRunner

from app.jobs import ats, sweep
from app.jobs.models import Sweep
from app.jobs.sweep_router import sweep_out
from app.profile import schemas
from app.profile import service as profile_service
from tests.browser.fakes import FakeTools
from tests.jobs.sweep_helpers import board, read_sweep, search, set_profile
from tests.jobs.test_frozen_search_scope import CANARY, alice, fake_queue, site
from tests.jobs.test_sweep_browser_sources import offer, runner
from tests.jobs.test_sweep_browser_sources import site as browser_pages

__all__ = ["alice", "fake_queue"]


def start_everything(factory, alice, clock, queue):
    """The engine's start with every enabled source, through the shared `begin`."""
    return asyncio.run(
        sweep.begin(factory, queue, alice.id, clock(), sweep_out, websites_only=False)
    )


def mixed_search():
    return search(
        board("fake-api"),
        feeds=[{"kind": "remoteok", "enabled": True}],
        browser_sources=[
            {"kind": "google_careers", "query": "ai", "enabled": True},
            {"kind": "site", "query": "https://legacy.example.org/", "enabled": True},
        ],
        interactive_sites=[site()],
        interactive_instructions=CANARY,
    )


@pytest.mark.parametrize("ending", ["done", "fail", "reap", "crash", "queue"])
def test_all_selected_sources_survive_start_reopened_and_terminal_views(
    ending, factory, clock, api, alice, fake_queue
):
    set_profile(factory, alice.id, mixed_search())
    fake_queue.down = ending == "queue"
    begun = start_everything(factory, alice, clock, fake_queue)
    assert begun.outcome == ("queue_down" if ending == "queue" else "started")
    with factory() as db:
        row = db.query(Sweep).filter_by(user_id=alice.id).one()
        sweep_id = row.id
    body = sweep_out(row).model_dump(mode="json")
    assert body["search_scope"]["source_count"] == 5
    entries = body["sources"]
    assert len(entries) == 5
    expected = {(entry["source_id"], entry["mode"]) for entry in entries}
    assert len(expected) == 5
    assert {mode for _, mode in expected} == {
        "api",
        "feed",
        "fixed_reader",
        "legacy_finder",
        "llm_browser",
    }
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
        assert len(actual["sources"]) == 5
        assert {entry["phase"] for entry in actual["sources"]} == {"done"}
    events = api.request("GET", f"/api/jobs/sweeps/{sweep_id}/events", token=alice.token)
    assert CANARY not in events.text
    for source_id, _ in expected:
        assert source_id in events.text


def test_colon_neighbors_have_distinct_stable_source_identities(factory, clock, alice):
    selected = search(
        browser_sources=[
            {
                "kind": "site",
                "query": "https://careers.example.org/a:b",
                "location": "c",
                "enabled": True,
            },
            {
                "kind": "site",
                "query": "https://careers.example.org/a",
                "location": "b:c",
                "enabled": True,
            },
            {
                "kind": "site",
                "query": "https://careers.example.org/a:b",
                "location": "c",
                "enabled": True,
            },
        ]
    )
    set_profile(factory, alice.id, selected)
    begun = sweep._insert(factory, alice.id, clock(), sweep_out, websites_only=False)
    first = read_sweep(factory, begun.sweep_id).stats["search_scope"]["sources"]
    assert len(first) == 2
    assert len({entry["source_id"] for entry in first}) == 2
    sweep.finish_done(factory, begun.sweep_id, sweep.Stats(), clock())
    again = sweep._insert(factory, alice.id, clock(), sweep_out, websites_only=False)
    assert read_sweep(factory, again.sweep_id).stats["search_scope"]["sources"] == first


def test_reader_updates_bind_to_frozen_entries_without_duplicate_or_counter_loss(
    factory, clock, api, alice, fake_queue, monkeypatch
):
    set_profile(factory, alice.id, mixed_search())
    sweep_id = start_everything(factory, alice, clock, fake_queue).sweep_id
    initial = read_sweep(factory, sweep_id).stats["sources"]
    snapshots = []
    original = sweep.save_progress

    def record(sessions, row_id, stats, now):
        original(sessions, row_id, stats, now)
        snapshots.append(copy.deepcopy(read_sweep(factory, row_id).stats["sources"]))

    monkeypatch.setattr(sweep, "save_progress", record)

    class EmptyAts:
        async def fetch_board(self, *args):
            return ats.BoardResult()

    async def feed(client, config):
        return []

    feeds = FeedRunner(
        lambda: FeedClient(stub_base_url="http://feeds.invalid"), read={"remoteok": feed}
    )
    browser = runner(FakeTools(browser_pages(offer("AI Engineer"))))
    asyncio.run(sweep.run_sweep(factory, sweep_id, EmptyAts(), clock, feeds=feeds, browser=browser))
    row = read_sweep(factory, sweep_id)
    assert sweep._stats_json(row, sweep.Stats())["sources"] == row.stats["sources"]
    expected_ids = {entry["source_id"] for entry in initial}
    for entries in [*snapshots, row.stats["sources"]]:
        assert len(entries) == 5
        assert {entry["source_id"] for entry in entries} == expected_ids
        assert all(entry["mode"] for entry in entries)
    final = {entry["mode"]: entry for entry in row.stats["sources"]}
    assert final["fixed_reader"]["postings"] == row.stats["new"] == 1
    assert final["fixed_reader"]["found"] == 1
    assert row.stats["boards_ok"] == row.stats["boards_done"] == 1
    assert final["api"]["outcome"] == final["feed"]["outcome"] == "no_matches"
    assert final["legacy_finder"]["reason"] == "no_credential"
    # Several titles and places are one agent's to search now (owner, 2026-10-27, jobs J18), so
    # the website reaches the model check, and this run has no model access.
    assert final["llm_browser"]["reason"] == "no_credential"
    assert all(entry["phase"] == "done" for entry in final.values())
    assert any(any(entry["phase"] == "running" for entry in entries) for entries in snapshots)


def test_held_edit_wins_before_start_and_returns_revision_conflict(factory, clock, alice):
    set_profile(factory, alice.id, search(board("fake-before")))
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
                schemas.validate_section("search", search(board("fake-after"))),
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
    set_profile(factory, alice.id, search(board("fake-before")))
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
                schemas.validate_section("search", search(board("fake-after"))),
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
            started = pool.submit(sweep._insert, factory, alice.id, clock(), view, 1, False)
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
        assert row.stats["search_scope"]["search"]["boards"][0]["org"] == "fake-before"
        claimed = sweep.claim(factory, begun.sweep_id, clock())
        assert claimed.boards[0].org == "fake-before"
    finally:
        sa.event.remove(engine, "before_cursor_execute", observe)
