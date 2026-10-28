"""A permission-only switch (LinkedIn's or Free-Work's consent row) is never a source: only a
ticked job website is searched."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.jobs import sweep
from app.jobs.models import SweepStatus, SweepTrigger
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, FakeTools
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import read_sweep, set_profile, website_runner
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_sweep_api import FakeQueue, fake_queue

__all__ = ["fake_queue"]


@pytest.fixture
def owner(factory: Any, api: Api) -> Signed:
    return sign_in(factory, api, "e2e-intent-owner@example.org")


def entry(kind: str, flag: bool = True) -> dict[str, Any]:
    return {
        "kind": kind,
        "query": "Robotics",
        "enabled": True,
        "interactive_only": flag,
        "accepted_at": "2026-10-08T09:30:00+00:00",
        "text_version": 1,
    }


def selected(kind: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "platform": kind,
        "url": "https://linkedin.com/jobs" if kind == "linkedin" else "https://free-work.com/jobs",
        "enabled": enabled,
        "mode": "llm_browser",
    }


@pytest.mark.parametrize("kind", ["linkedin", "freework"])
@pytest.mark.parametrize("selection", ["enabled", "disabled", "removed"])
def test_permission_rows_never_add_legacy_frozen_modes(kind: str, selection: str) -> None:
    search = SearchSection.model_validate(
        {
            "browser_sources": [entry(kind)],
            "interactive_sites": []
            if selection == "removed"
            else [selected(kind, selection == "enabled")],
        }
    )
    assert sweep.has_sources(search) is (selection == "enabled")
    scope = sweep.freeze_scope(
        type("ProfileView", (), {"sections": {"search": search.model_dump()}, "revision": 1})(),
        SweepTrigger.manual,
        datetime.now(UTC),
    )
    assert [row["mode"] for row in scope["sources"]] == (
        ["llm_browser"] if selection == "enabled" else []
    )


@pytest.mark.parametrize("kind", ["linkedin", "freework"])
@pytest.mark.parametrize("selection", ["enabled", "disabled", "removed"])
def test_real_runner_and_sweep_do_not_fall_back_after_setup_changes(
    api: Api,
    owner: Signed,
    factory: Any,
    clock: Any,
    fake_queue: FakeQueue,
    kind: str,
    selection: str,
) -> None:
    set_profile(factory, owner.id, {"interactive_sites": [selected(kind)]})
    accepted = api.request(
        "PUT",
        "/api/browser/linkedin" if kind == "linkedin" else f"/api/opportunities/platforms/{kind}",
        token=owner.token,
        json={
            "enabled": True,
            "query": "Robotics",
            "accept_version": 1,
            "interactive_only": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    current = api.get("/api/profile", token=owner.token).json()
    search = current["sections"]["search"]
    search["interactive_sites"] = (
        [] if selection == "removed" else [selected(kind, selection == "enabled")]
    )
    saved = api.request(
        "PUT",
        "/api/profile/search",
        token=owner.token,
        json={"data": search, "base_revision": current["revision"]},
    )
    assert saved.status_code == 200, saved.text
    started = asyncio.run(sweep.begin(factory, fake_queue, owner.id, clock(), lambda row: row.id))
    if selection != "enabled":
        # The permission row alone is no website to search.
        assert started.outcome == "no_websites"
        assert fake_queue.calls == []
        return
    assert started.outcome == "started"
    run_id = started.sweep_id
    assert run_id is not None
    frozen = read_sweep(factory, run_id).stats["search_scope"]
    assert [row["mode"] for row in frozen["sources"]] == ["llm_browser"]
    leases, tools = FakeLeases(), FakeTools({})
    asyncio.run(sweep.run_sweep(factory, run_id, clock, browser=website_runner(tools, leases)))
    completed = read_sweep(factory, run_id)
    assert completed.status is SweepStatus.done
    [interactive] = completed.stats["sources"]
    assert interactive["mode"] == "llm_browser"
    assert interactive["outcome"] == "failed"
    assert interactive["reason"] == "permission_unavailable"
    assert leases.acquired == []
    assert tools.calls == []


@pytest.mark.parametrize("kind", ["linkedin", "freework"])
def test_permission_alone_is_not_a_source_for_a_new_run(
    api: Api,
    owner: Signed,
    factory: Any,
    clock: Any,
    fake_queue: FakeQueue,
    kind: str,
) -> None:
    set_profile(factory, owner.id, {"browser_sources": [entry(kind)]})
    response = api.post("/api/jobs/sweeps", token=owner.token)
    assert response.status_code == 422, response.text
    assert fake_queue.calls == []
    scope = sweep.freeze_scope(
        type(
            "ProfileView",
            (),
            {"sections": {"search": {"browser_sources": [entry(kind)]}}, "revision": 1},
        )(),
        SweepTrigger.manual,
        clock(),
    )
    assert scope["sources"] == []
