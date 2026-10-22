"""Actual Manager/Live/tool routes share one input owner, including private and gated acts."""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import app as browser_app
from browserd.leases import LeaseError, Request
from browserd.tools import actions, linkedin, pacing, routes

from tests.browser.test_b4_live_unit import VIEWER, World, run
from tests.browser.test_b5_actions import Page, acted, facts, tab_of


@pytest.fixture
def navigation_errors(monkeypatch):
    # The API environment omits the browser image's Playwright package. These two exception
    # types are its only dependency on this fake page; pages.navigate itself remains real.
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(Error=RuntimeError, TimeoutError=TimeoutError),
    )


@pytest.mark.parametrize("give_back", [False, True])
@pytest.mark.parametrize("boundary", ["dwell", "front"])
def test_navigation_rechecks_live_ownership_after_each_await(
    monkeypatch, navigation_errors, boundary, give_back
):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page = Page()
        w.manager.browser.browser = tab_of(page).browser.browser
        navigated = []

        def take():
            w.live.take(lease.lease_id, VIEWER)
            if give_back:
                w.live.give_back(lease.lease_id)

        async def dwell(_):
            if boundary == "dwell":
                take()

        fronts = 0

        async def front():
            nonlocal fronts
            fronts += 1
            if boundary == "front" and fronts == 2:
                take()

        async def goto(url, **kwargs):
            navigated.append(url)

        monkeypatch.setattr(page, "bring_to_front", front)
        monkeypatch.setattr(page, "goto", goto)
        monkeypatch.setattr(routes, "PACER", pacing.Pacer(sleep=dwell))
        result = (await call(svc, lease, headers, "browser_navigate", {"url": "http://pages/new"}))[
            1
        ]
        assert result.get("error") == "person_input", result
        assert navigated == []

    run(go)


@pytest.mark.parametrize("action", ["select", "close", "list"])
@pytest.mark.parametrize("give_back", [False, True])
def test_tab_mutations_recheck_the_live_owner_after_settle(monkeypatch, action, give_back):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page, other = Page(), Page()
        context = tab_of(page).browser.browser
        context.contexts[0].pages.append(other)
        w.manager.browser.browser = context
        state = routes.state_of(lease)
        state.page = page
        fronts = []

        async def front():
            fronts.append(other)

        original = actions.Tab.settle

        async def settle(tab, result):
            answer = await original(tab, result)
            w.live.take(lease.lease_id, VIEWER)
            if give_back:
                w.live.give_back(lease.lease_id)
            return answer

        monkeypatch.setattr(other, "bring_to_front", front)
        monkeypatch.setattr(actions.Tab, "settle", settle)
        result = (await call(svc, lease, headers, "browser_tabs", {"action": action, "index": 1}))[
            1
        ]
        if action == "list":
            assert result["ok"]
            assert len(result["tabs"]) == 2
        else:
            assert result.get("error") == "person_input", result
        assert fronts == []
        assert not other.closed
        assert state.page is page

    run(go)


@pytest.mark.parametrize("purpose", ["apply_fill", "apply_manual"])
def test_unchanged_and_manual_person_time_navigation_still_reaches_the_page(
    monkeypatch, navigation_errors, purpose
):
    async def go(w):
        svc, lease, headers = await acquire(w, purpose)
        page = Page()
        page.on = lambda *_: None  # Apply myself installs its file chooser listener.
        w.manager.browser.browser = tab_of(page).browser.browser

        async def no_sleep(_):
            pass

        monkeypatch.setattr(routes, "PACER", pacing.Pacer(sleep=no_sleep))
        assert not lease.app_turn
        result = (await call(svc, lease, headers, "browser_navigate", {"url": "http://pages/new"}))[
            1
        ]
        assert result["ok"], result
        assert page.url == "http://pages/new"

    run(go)


@pytest.mark.parametrize("give_back", [False, True])
def test_tab_selection_does_not_commit_stale_state_after_awaited_focus(monkeypatch, give_back):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page, other = Page(), Page()
        browser = tab_of(page).browser.browser
        browser.contexts[0].pages.append(other)
        w.manager.browser.browser = browser
        state = routes.state_of(lease)
        state.page = page

        async def front():
            w.live.take(lease.lease_id, VIEWER)
            if give_back:
                w.live.give_back(lease.lease_id)

        monkeypatch.setattr(other, "bring_to_front", front)
        result = (
            await call(svc, lease, headers, "browser_tabs", {"action": "select", "index": 1})
        )[1]
        assert result.get("error") == "person_input", result
        assert state.page is page

    run(go)


async def acquire(w: World, purpose: str):
    request = Request.parse(
        {
            "user_id": "e2e-live-owner",
            "profile_id": str(uuid.uuid4()),
            "uid": 20000,
            "purpose": purpose,
            "hosts": ["pages"],
        }
    )
    lease, token = await w.manager.acquire(request)
    svc = browser_app.Service(w.manager, b"e2e-live-ownership")
    svc.live = w.live
    headers = {"x-lease-token": token}
    if capability := routes.issue_capability(lease):
        headers["x-gate-capability"] = capability
    return svc, lease, headers


async def call(svc, lease, headers, name="browser_click", body=None):
    request = browser_app.HttpRequest(
        "POST", "/", headers, json.dumps(body or {"ref": "e1"}).encode()
    )
    return await routes.call_tool(svc, request, {"name": name, "lease_id": lease.lease_id})


@pytest.mark.parametrize("purpose", ["apply_fill", "apply_submit", "profile_edit"])
def test_takeover_waits_then_requires_a_fresh_page_before_any_input(monkeypatch, purpose):
    calls = []

    async def action(tab, name, args):
        if name != "browser_snapshot":
            tab.require_input()
        calls.append(name)
        return {"ok": True, "snapshot": "Person changed the page", "title": "Changed page"}

    monkeypatch.setattr(actions, "run", action)

    async def go(w):
        svc, lease, headers = await acquire(w, purpose)
        w.live.take(lease.lease_id, VIEWER)
        pending = asyncio.create_task(call(svc, lease, headers))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not pending.done(), "automation acted while the live viewer owned input"
        w.live.give_back(lease.lease_id, str(uuid.uuid4()))
        assert w.live.holder(lease.lease_id) == VIEWER
        w.live.give_back(lease.lease_id)
        status, result = await pending
        assert status == 200
        assert result["error"] == "person_used"
        assert calls == ["browser_snapshot"]
        assert "Changed page" in result["message"]
        assert (await call(svc, lease, headers))[1]["ok"]
        assert calls[-1] == "browser_click"

    run(go)


@pytest.mark.parametrize("purpose", ["apply_fill", "apply_submit", "profile_edit"])
@pytest.mark.parametrize("give_back_during_pacing", [False, True])
def test_real_actions_refuse_takeover_during_pacing_even_after_give_back(
    monkeypatch,
    purpose,
    give_back_during_pacing,
):
    async def go(w):
        svc, lease, headers = await acquire(w, purpose)
        page = Page()
        page.add(
            "e1",
            facts("Save" if purpose == "profile_edit" else "Submit", tag="button", kind="submit"),
        )
        # The production Tab/actions run against the normal page double; ownership and lease
        # cancellation are the actual implementations, not replaced callbacks.
        w.manager.browser.browser = SimpleNamespace(
            contexts=[tab_of(page).browser.browser.contexts[0]]
        )

        async def takeover(_):
            w.live.take(lease.lease_id, VIEWER)
            if give_back_during_pacing:
                w.live.give_back(lease.lease_id)

        monkeypatch.setattr(routes, "PACER", pacing.Pacer(sleep=takeover))
        if purpose == "apply_fill":
            page.elements["aria-ref=e1"].f = facts("Next", tag="button", kind="button")
        status, result = await call(svc, lease, headers)
        assert status == 200
        assert result["error"] == "person_input"
        assert acted(page) == []

    run(go)


def test_private_opener_waits_and_uses_the_same_live_owner(monkeypatch):
    opened = []

    async def opener(tab, job_id, fixture_base):
        tab.require_input()
        opened.append(job_id)
        return {"ok": True}

    async def action(tab, name, args):
        return {"ok": True, "title": "Changed page", "snapshot": "Changed page"}

    async def settle(tab, result):
        return result

    monkeypatch.setattr(linkedin, "open_modal", opener)
    monkeypatch.setattr(actions, "run", action)
    monkeypatch.setattr(actions.Tab, "settle", settle)

    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        req = browser_app.HttpRequest("POST", "/", headers, b'{"job_id":"123"}')
        w.live.take(lease.lease_id, VIEWER)
        pending = asyncio.create_task(routes.linkedin_open(svc, req, {"lease_id": lease.lease_id}))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not pending.done()
        w.live.give_back(lease.lease_id)
        assert (await pending)[1]["error"] == "person_used"
        assert opened == []

    run(go)


def test_private_opener_cannot_keep_input_after_takeover_and_give_back(monkeypatch):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")

        async def opener(tab, job_id, fixture_base):
            w.live.take(lease.lease_id, VIEWER)
            w.live.give_back(lease.lease_id)
            tab.require_input()
            raise AssertionError("opener must stop before clicking")

        monkeypatch.setattr(linkedin, "open_modal", opener)
        req = browser_app.HttpRequest("POST", "/", headers, b'{"job_id":"123"}')
        assert (await routes.linkedin_open(svc, req, {"lease_id": lease.lease_id}))[1][
            "error"
        ] == "person_input"

    run(go)


def test_lease_end_cancels_a_tool_waiting_for_give_back(monkeypatch):
    async def action(tab, name, args):
        raise AssertionError("ended lease cannot act")

    monkeypatch.setattr(actions, "run", action)

    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        w.live.take(lease.lease_id, VIEWER)
        pending = asyncio.create_task(call(svc, lease, headers))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not pending.done()
        await w.manager.release(lease.lease_id, "cancelled")
        with pytest.raises(LeaseError) as exc:
            await pending
        assert exc.value.status == 410

    run(go)


def test_takeover_pauses_active_and_idle_clocks_and_expiry_restores_the_agent(monkeypatch):
    async def action(tab, name, args):
        return {"ok": True, "title": "Changed page"}

    monkeypatch.setattr(actions, "run", action)

    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        w.clock.now += 5
        w.live.take(lease.lease_id, VIEWER)
        before = lease.active_s(w.clock())
        w.clock.now += 900
        assert lease.expired(w.clock()) is None
        assert lease.active_s(w.clock()) == before
        w.live.tick()
        assert w.live.holder(lease.lease_id) is None
        assert lease.state == "active"
        assert lease.expired(w.clock()) is None
        assert (await call(svc, lease, headers))[1]["error"] == "person_used"

    run(go)
