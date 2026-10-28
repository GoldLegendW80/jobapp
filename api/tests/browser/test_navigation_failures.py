"""Navigation's Playwright failures keep their retryable code across awaited boundaries."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import actions, pacing, routes

from tests.browser.test_b4_live_unit import run
from tests.browser.test_b5_actions import Page, tab_of
from tests.browser.test_live_tool_ownership import acquire, call


class PageError(Exception):
    pass


class PageTimeoutError(PageError):
    pass


@pytest.fixture
def navigation_errors(monkeypatch):
    # The API environment omits the browser image's Playwright dependency.
    try:
        import playwright.async_api as playwright_api
        from playwright._impl import _errors
    except ModuleNotFoundError:
        errors = SimpleNamespace(
            Error=PageError, TimeoutError=PageTimeoutError, TargetClosedError=PageError
        )
    else:
        errors = SimpleNamespace(
            Error=playwright_api.Error,
            TimeoutError=playwright_api.TimeoutError,
            TargetClosedError=_errors.TargetClosedError,
        )
    monkeypatch.setitem(sys.modules, "playwright.async_api", errors)
    return errors


@pytest.mark.parametrize(
    "boundary", ["guard", "front1", "front2", "goto", "title", "settle1", "settle2"]
)
@pytest.mark.parametrize("error_name", ["Error", "TimeoutError", "TargetClosedError"])
def test_navigation_playwright_errors_are_navigation_failures(
    monkeypatch, navigation_errors, boundary, error_name
):
    error_type = getattr(navigation_errors, error_name)

    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page = Page()
        w.manager.browser.browser = tab_of(page).browser.browser
        monkeypatch.setattr(routes, "PACER", pacing.Pacer(pacing_sleep))
        fronts = settles = 0

        async def guard(*args):
            if boundary == "guard":
                raise error_type("closed target")

        async def front():
            nonlocal fronts
            fronts += 1
            if boundary == f"front{fronts}":
                raise error_type("closed target")

        async def goto(url, **kwargs):
            if boundary == "goto":
                raise error_type("closed target")

        async def title():
            if boundary == "title":
                raise error_type("closed target")
            return "Form"

        async def evaluate(js):
            assert js == "window.stop()"

        original = actions.Tab.settle

        async def settle(tab, result):
            nonlocal settles
            settles += 1
            if boundary == f"settle{settles}":
                raise error_type("closed target")
            return await original(tab, result)

        monkeypatch.setattr(page, "bring_to_front", front)
        monkeypatch.setattr(w.manager.browser.browser.contexts[0], "route", guard)
        monkeypatch.setattr(page, "goto", goto)
        monkeypatch.setattr(page, "title", title)
        monkeypatch.setattr(page, "evaluate", evaluate, raising=False)
        monkeypatch.setattr(actions.Tab, "settle", settle)
        status, result = await call(svc, lease, headers, "browser_navigate", {"url": page.url})
        expected = (
            "navigation_timeout"
            if boundary == "goto" and error_name == "TimeoutError"
            else "navigation_failed"
        )
        assert status == 200
        assert result["ok"] is False
        assert result["error"] == expected

    run(go)


async def pacing_sleep(_):
    pass


class TargetClosedError(RuntimeError):
    """A Python lookalike must not be mistaken for a Playwright failure."""


@pytest.mark.parametrize(
    "error", [RuntimeError("bug"), TargetClosedError("closed target"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("tool", ["browser_navigate", "browser_snapshot"])
def test_non_playwright_errors_and_cancellation_keep_their_route_contract(
    monkeypatch, navigation_errors, error, tool
):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page = Page()
        w.manager.browser.browser = tab_of(page).browser.browser

        async def front():
            raise error

        monkeypatch.setattr(page, "bring_to_front", front)
        request = call(svc, lease, headers, tool, {"url": page.url})
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            assert (await request)[1]["error"] == "page_error"

    run(go)


def test_non_navigation_playwright_error_still_returns_page_error(monkeypatch, navigation_errors):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page = Page()
        w.manager.browser.browser = tab_of(page).browser.browser

        async def front():
            raise navigation_errors.Error("closed target")

        monkeypatch.setattr(page, "bring_to_front", front)
        assert (await call(svc, lease, headers, "browser_snapshot", {}))[1]["error"] == "page_error"

    run(go)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (actions.OffList(), "host_not_allowed"),
        (actions.PersonInput(), "person_input"),
        (ValueError("bad input"), "bad_args"),
    ],
)
def test_navigation_preserves_policy_and_argument_refusals(
    monkeypatch, navigation_errors, error, code
):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        page = Page()
        w.manager.browser.browser = tab_of(page).browser.browser

        async def front():
            raise error

        monkeypatch.setattr(page, "bring_to_front", front)
        assert (await call(svc, lease, headers, "browser_navigate", {"url": page.url}))[1][
            "error"
        ] == code

    run(go)


def test_returned_page_error_is_not_reclassified(monkeypatch, navigation_errors):
    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")

        async def failed(tab, args):
            return {"ok": False, "error": "page_error"}

        monkeypatch.setitem(actions.TOOLS, "browser_navigate", failed)
        assert (await call(svc, lease, headers, "browser_navigate", {}))[1] == {
            "ok": False,
            "error": "page_error",
        }

    run(go)


def refusal_recovery_tab(monkeypatch, scenario, error):
    """Real Tab policy checks and repair, with only the page transport replaced."""
    target = "http://pages/form"
    off = "http://off.example/form"
    page = Page(off if scenario == "existing_off_list" else target)
    tab = tab_of(page)
    trace = []

    async def go_back(**kwargs):
        trace.append("recover_back")
        raise RuntimeError("no history")

    async def goto(url, **kwargs):
        if scenario == "redirect_off_list" and url == target:
            trace.append("redirect_off_list")
            page.url = off
            return
        assert url == actions.ABOUT_BLANK
        trace.append("recover_blank")
        if error is not None:
            raise error
        page.url = url

    async def close():
        trace.append("close_secondary")
        if error is not None:
            raise error
        secondary.closed = True

    monkeypatch.setattr(page, "go_back", go_back)
    monkeypatch.setattr(page, "goto", goto)
    if scenario == "off_list_secondary":
        tab.state.blocked.append(off)
        secondary = Page(off)
        monkeypatch.setattr(secondary, "close", close)
        tab.browser.browser.contexts[0].pages.append(secondary)
    return tab, trace


@pytest.mark.parametrize(
    "scenario", ["existing_off_list", "off_list_secondary", "redirect_off_list"]
)
@pytest.mark.parametrize("error_name", ["Error", "TimeoutError", "TargetClosedError", None])
def test_navigation_refusal_recovery_keeps_its_route_contract(
    monkeypatch, navigation_errors, scenario, error_name
):
    error = getattr(navigation_errors, error_name)("repair failed") if error_name else None
    tab, trace = refusal_recovery_tab(monkeypatch, scenario, error)

    async def go(w):
        svc, lease, headers = await acquire(w, "apply_fill")
        monkeypatch.setattr(routes, "_tab", lambda *args, **kwargs: tab)
        status, result = await call(
            svc, lease, headers, "browser_navigate", {"url": "http://pages/form"}
        )
        assert status == 200
        # A repair that failed is its own code, never a page to recover on (PR 720 round 2).
        assert result["error"] == ("guard_failed" if error else "host_not_allowed")
        assert trace == (
            ["close_secondary"]
            if scenario == "off_list_secondary"
            else (["redirect_off_list"] if scenario == "redirect_off_list" else [])
            + ["recover_back", "recover_blank"]
        )

    run(go)


@pytest.mark.parametrize(
    "scenario", ["existing_off_list", "off_list_secondary", "redirect_off_list"]
)
@pytest.mark.parametrize(
    "error_name", ["Error", "TimeoutError", "TargetClosedError", "RuntimeError", "CancelledError"]
)
def test_navigation_refusal_recovery_preserves_the_original_exception(
    monkeypatch, navigation_errors, scenario, error_name
):
    error_type = {
        "RuntimeError": RuntimeError,
        "CancelledError": asyncio.CancelledError,
    }.get(error_name) or getattr(navigation_errors, error_name)
    error = error_type("repair failed")
    tab, _ = refusal_recovery_tab(monkeypatch, scenario, error)

    async def go():
        with pytest.raises(error_type) as caught:
            await actions.run(tab, "browser_navigate", {"url": "http://pages/form"})
        assert caught.value is error
        if not isinstance(error, asyncio.CancelledError):  # never wrapped, so never noted
            assert actions.GUARD_FAILED_NOTE in caught.value.__notes__

    asyncio.run(go())
