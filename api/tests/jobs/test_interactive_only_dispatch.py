"""A permission-only switch does not become a reader when interactive selection is removed."""

import asyncio
import uuid
from typing import Any

import pytest

from app.browser import sources
from app.jobs import ats, sweep
from app.jobs.models import SweepStatus, SweepTrigger
from app.profile.schemas import SearchSection
from tests.browser.test_b8_sources import FakeLeases, FakeTools
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import board, read_sweep, set_profile
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
    assert sweep.enabled_sources(search) == []
    assert sweep.has_sources(search) is (selection == "enabled")


@pytest.mark.parametrize("kind", ["linkedin", "freework"])
@pytest.mark.parametrize("selection", ["enabled", "disabled", "removed"])
def test_real_runner_and_sweep_do_not_fall_back_after_setup_changes(
    api: Api,
    owner: Signed,
    factory: Any,
    clock: Any,
    fake_queue: FakeQueue,
    monkeypatch: Any,
    kind: str,
    selection: str,
) -> None:
    set_profile(
        factory, owner.id, {"boards": [board("fake-intent")], "interactive_sites": [selected(kind)]}
    )
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
    started = api.post("/api/jobs/sweeps", token=owner.token)
    assert started.status_code == 202, started.text
    run_id = uuid.UUID(started.json()["id"])
    frozen = read_sweep(factory, run_id).stats["search_scope"]
    assert not any(row["mode"] in ("fixed_reader", "legacy_finder") for row in frozen["sources"])
    assert len(frozen["sources"]) == (2 if selection == "enabled" else 1)

    async def forbidden_legacy(*args: Any, **kwargs: Any) -> None:
        pytest.fail("a permission-only row activated a legacy reader")

    monkeypatch.setattr(sources.Runner, "_one", forbidden_legacy)

    class BoardClient:
        async def fetch_board(self, ats_name: str, org: str) -> ats.BoardResult:
            assert (ats_name, org) == ("ashby", "fake-intent")
            return ats.BoardResult()

    leases, tools = FakeLeases(), FakeTools({})
    runner = sources.Runner(leases, lambda *_: tools)
    asyncio.run(sweep.run_sweep(factory, run_id, BoardClient(), clock, browser=runner))
    completed = read_sweep(factory, run_id)
    assert completed.status is SweepStatus.done
    assert completed.stats["boards_ok"] == 1
    modes = [row["mode"] for row in completed.stats["sources"]]
    assert modes == (["api", "llm_browser"] if selection == "enabled" else ["api"])
    if selection == "enabled":
        interactive = completed.stats["sources"][1]
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
