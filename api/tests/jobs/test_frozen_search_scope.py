"""Start freezes intent before queueing; status exposes only the owner-safe projection."""

import asyncio
import copy
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.identity.api_helpers import Api, Clock
from tests.jobs.sweep_helpers import board, read_sweep, search, set_profile
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_sweep_api import FakeQueue, fake_queue

__all__ = ["fake_queue"]
CANARY = "scope-instruction-7429faae"


def site(**changes: Any) -> dict[str, Any]:
    return {
        "platform": "custom",
        "url": "https://jobs.example.org/?q=" + CANARY,
        "enabled": True,
        "mode": "llm_browser",
        **changes,
    }


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j629-alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j629-bob@example.org")


def start(api: Api, who: Signed, **kwargs: Any) -> Any:
    return api.request("POST", "/api/jobs/sweeps", token=who.token, **kwargs)


def test_start_revision_and_owner_safe_reopened_views(
    clock: Clock,
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    bob: Signed,
    fake_queue: FakeQueue,
) -> None:
    selected = search(
        interactive_sites=[site(), site(url="https://disabled.example.org/", enabled=False)],
        interactive_instructions=CANARY,
    )
    set_profile(factory, alice.id, selected)
    rejected = start(api, alice, json={"profile_revision": 0})
    assert rejected.status_code == 409
    assert fake_queue.calls == []
    assert start(api, alice, json={"profile_revision": True}).status_code == 422
    created = start(api, alice, json={"profile_revision": 1})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["search_scope"]["profile_revision"] == 1
    assert body["search_scope"]["criteria"]["days"] == selected["days"]
    assert len(body["sources"]) == 1
    entry = body["sources"][0]
    assert (entry["mode"], entry["phase"], entry["outcome"], entry["reason"]) == (
        "llm_browser",
        "pending",
        "not_attempted",
        None,
    )
    assert entry["source_id"]
    assert entry["host"] == "jobs.example.org"
    assert CANARY not in created.text
    sweep_id = uuid.UUID(body["id"])
    scope = read_sweep(factory, sweep_id).stats["search_scope"]
    assert scope["search"]["interactive_instructions"] == CANARY
    assert scope["trigger"] == "manual"
    assert scope["version"] == 1
    assert len(scope["sources"]) == 1
    for path in ("/api/jobs/sweeps", f"/api/jobs/sweeps/{sweep_id}"):
        response = api.request("GET", path, token=alice.token)
        assert response.status_code == 200
        assert CANARY not in response.text
        assert response.headers["cache-control"] == "no-store"
    for suffix in ("", "/events"):
        assert (
            api.request("GET", f"/api/jobs/sweeps/{sweep_id}{suffix}", token=bob.token).status_code
            == 404
        )
    assert api.request("GET", "/api/jobs/sweeps", token=bob.token).json()["items"] == []
    sweep.finish_done(factory, sweep_id, sweep.Stats(), clock())
    streamed = api.request("GET", f"/api/jobs/sweeps/{sweep_id}/events", token=alice.token)
    assert "event: done" in streamed.text
    assert CANARY not in streamed.text
    assert '"search_scope"' in streamed.text
    assert '"source_id"' in streamed.text


def test_edits_before_claim_and_during_work_only_change_later_sweeps(
    clock: Clock,
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
) -> None:
    original = search(
        board("fake-original"), interactive_sites=[site()], interactive_instructions=CANARY
    )
    set_profile(factory, alice.id, original)
    sweep_id = uuid.UUID(start(api, alice).json()["id"])
    frozen = copy.deepcopy(read_sweep(factory, sweep_id).stats["search_scope"])
    set_profile(factory, alice.id, search(board("fake-before-claim"), days=2))
    claimed = sweep.claim(factory, sweep_id, clock())
    assert claimed is not None
    assert [b.org for b in claimed.boards] == ["fake-original"]
    assert claimed.search.interactive_instructions == CANARY
    set_profile(factory, alice.id, search(board("fake-during-run"), days=7))
    sweep.save_progress(factory, sweep_id, sweep.Stats(seen=3), clock())
    assert read_sweep(factory, sweep_id).stats["search_scope"] == frozen
    sweep.finish_done(factory, sweep_id, sweep.Stats(seen=3), clock())
    assert read_sweep(factory, sweep_id).stats["search_scope"] == frozen
    later = uuid.UUID(start(api, alice).json()["id"])
    next_plan = sweep.claim(factory, later, clock())
    assert next_plan is not None
    assert [b.org for b in next_plan.boards] == ["fake-during-run"]
    assert next_plan.filter.days == 7


@pytest.mark.parametrize("path", ["fail", "reap", "crash", "queue"])
def test_stats_rewrites_retain_scope_and_initial_sources(
    clock: Clock,
    path: str,
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
) -> None:
    set_profile(factory, alice.id, search(interactive_sites=[site()]))
    if path == "queue":
        fake_queue.down = True
        assert start(api, alice).status_code == 503
        with factory() as db:
            row = db.query(Sweep).filter_by(user_id=alice.id).one()
            sweep_id = row.id
    else:
        sweep_id = uuid.UUID(start(api, alice).json()["id"])
    frozen = copy.deepcopy(read_sweep(factory, sweep_id).stats["search_scope"])
    if path == "fail":
        sweep.fail_sweep(factory, sweep_id, sweep.INTERNAL, clock(), sweep.Stats(new=2))
    elif path == "reap":
        with factory() as db:
            assert sweep.reap_stale(db, clock() + timedelta(hours=1), alice.id) == 1
            db.commit()
    elif path == "crash":
        assert sweep.claim(factory, sweep_id, clock()) is not None
        assert sweep.claim(factory, sweep_id, clock()) is None
    row = read_sweep(factory, sweep_id)
    assert row.stats["search_scope"] == frozen
    assert len(row.stats["sources"]) == 1
    assert row.status is SweepStatus.failed


def test_scheduled_creation_freezes_current_revision_and_keeps_slot(
    clock: Clock,
    factory: sessionmaker[Session],
    alice: Signed,
) -> None:
    tick = clock().replace(hour=9, minute=2, second=0, microsecond=0)
    selected = search(
        interactive_sites=[site()],
        sweep_schedule={"enabled": True, "times": ["09:00"], "timezone": "UTC"},
    )
    set_profile(factory, alice.id, selected)
    with factory() as db:
        [due] = sweep.scheduled_due(db, tick)
        row = sweep.start_scheduled(db, due, tick)
        assert row is not None
        sweep_id = row.id
        db.commit()
    frozen = copy.deepcopy(read_sweep(factory, sweep_id).stats["search_scope"])
    assert frozen["trigger"] == "schedule"
    assert frozen["profile_revision"] == 1
    set_profile(factory, alice.id, search(board("fake-later")))
    claimed = sweep.claim(factory, sweep_id, tick)
    assert claimed is not None
    assert claimed.boards == []
    assert claimed.schedule_slot == due.slot.key
    sweep.finish_done(factory, sweep_id, sweep.Stats(), tick)
    row = read_sweep(factory, sweep_id)
    assert row.stats["search_scope"] == frozen
    assert row.stats["schedule_slot"] == due.slot.key


def test_interactive_only_run_explains_unsupported_criteria_without_legacy_fallback(
    clock: Clock,
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Several titles and places are one agent's to search now (owner, 2026-10-27, jobs J18); a
    # regular expression that is not a plain word still cannot be typed into a website's search.
    title = {"terms": ["(ai|ml) engineer"], "advanced_regex": True}
    set_profile(
        factory,
        alice.id,
        search(interactive_sites=[site()], interactive_instructions=CANARY, title_include=title),
    )
    sweep_id = uuid.UUID(start(api, alice).json()["id"])

    class NoAts:
        async def fetch_board(self, *args: Any) -> None:
            pytest.fail("interactive intent reached an API reader")

    async def no_hook(*args: Any) -> None:
        pytest.fail("interactive intent reached a fixed source reader")

    from tests.browser.test_b8_sources import FakeTools
    from tests.jobs.test_sweep_browser_sources import runner

    asyncio.run(
        sweep.run_sweep(
            factory, sweep_id, NoAts(), clock, browser=runner(FakeTools({})), feeds=no_hook
        )
    )
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    [entry] = row.stats["sources"]
    assert entry["outcome"] == "failed"
    assert entry["reason"] == "unsupported_criteria"
    assert entry["phase"] == "done"
    assert CANARY not in caplog.text


def test_old_runs_without_scope_keep_explicit_claim_time_legacy_behavior(
    clock: Clock,
    factory: sessionmaker[Session],
    alice: Signed,
) -> None:
    set_profile(factory, alice.id, search(board("fake-current")))
    with factory() as db:
        row = Sweep(user_id=alice.id, trigger=SweepTrigger.manual, started_at=clock(), stats={})
        db.add(row)
        db.commit()
        sweep_id = row.id
    claimed = sweep.claim(factory, sweep_id, clock())
    assert claimed is not None
    assert claimed.boards[0].org == "fake-current"
    assert "search_scope" not in read_sweep(factory, sweep_id).stats
