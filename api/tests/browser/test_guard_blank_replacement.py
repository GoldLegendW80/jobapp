"""The actual memory guard and lease page listener share the internal blank tab contract.

Only the Playwright/process boundary is fake: new_page emits page before its response, and
closing the final page produces a clean process exit. Both listener/new_page return orderings are
explicit, with no sleeps or native browser. This does not remedy memory pressure or restore
the form that the guard intentionally closes.
"""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.browser.test_navigation_failures import navigation_errors as navigation_errors

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import chromium  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import Request  # noqa: E402
from browserd.tools import actions, pacing, policy  # noqa: E402

ON = "https://pages.example/form"


class Page:
    def __init__(self, context: "Context", url: str) -> None:
        self.context, self.url = context, url
        self.main_frame = SimpleNamespace(url=url)
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def wait_for_load_state(self, state: str, **kw: Any) -> None:
        assert state == "domcontentloaded"
        assert kw == {"timeout": 10_000}

    async def close(self) -> None:
        self.closed = True
        self.context.pages.remove(self)
        if not self.context.pages:
            self.context.exit.set_result(0)


class Context:
    def __init__(self, listener_before_return: bool) -> None:
        self.pages = [Page(self, ON)]
        self.listeners: dict[str, list[Callable[..., Any]]] = {}
        self.pending: list[asyncio.Future[Any]] = []
        self.listener_before_return = listener_before_return
        self.exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def route(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"
        self.route_handler = handler

    def on(self, event: str, listener: Callable[..., Any]) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def new_page(self, url: str = "about:blank") -> Page:
        page = Page(self, url)
        self.pages.append(page)
        # Playwright 1.55 _browser_context.py:_on_page appends the page then emits it.
        # Its new_page separately awaits a channel response; the scheduled listener can
        # finish before or after that coroutine returns to the guard.
        # https://github.com/microsoft/playwright-python/blob/v1.55.0/playwright/_impl/_browser_context.py
        for listener in self.listeners.get("page", []):
            self.pending.append(listener(page))
        if self.listener_before_return:
            await self.drain()
        return page

    async def drain(self) -> None:
        await asyncio.gather(*self.pending)
        self.pending.clear()


async def no_sleep(_: float) -> None:
    raise AssertionError("this lifecycle test must not use pacing")


async def setup(listener_before_return: bool) -> tuple[actions.Tab, Context]:
    context = Context(listener_before_return)
    request = Request.parse(
        {
            "user_id": "e2e-blank",
            "profile_id": "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11",
            "uid": 20001,
            "purpose": "apply_fill",
            "hosts": ["pages.example"],
        }
    )
    browser = chromium.Chromium(request, "blank-test", None, Config(secret=b"s"))
    browser.browser = SimpleNamespace(contexts=[context])
    browser.proc = SimpleNamespace(wait=lambda: asyncio.shield(context.exit))
    tab = actions.Tab(
        request=request,
        state=actions.State(page=context.pages[0]),
        browser=browser,
        pacer=pacing.Pacer(no_sleep),
        lease_tag="blank-test",
    )
    await tab.guard()  # Install the real route, redirect and page listeners.
    return tab, context


@pytest.mark.parametrize("listener_before_return", [True, False])
def test_guard_keeps_exact_blank_and_does_not_end_the_lease(listener_before_return: bool) -> None:
    async def go() -> None:
        tab, context = await setup(listener_before_return)
        victim = context.pages[0]
        events: list[dict[str, Any]] = []
        ended: list[str] = []

        async def end(reason: str) -> None:
            ended.append(reason)

        tab.browser.on_end = end
        limit = 1024 << 20
        assert await chromium.guard_memory(
            tab.browser.browser,
            90,
            lambda **event: events.append(event),
            lambda: (1006 << 20, limit),
        )
        await context.drain()
        assert victim.is_closed(), "the original form remains the guard's victim"
        assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1006}]
        if context.exit.done():
            await tab.browser._watch_exit()  # Real clean-exit path ends the lease on old source.
        assert ended == [], "the page listener removed the replacement and ended the lease"
        assert len(context.pages) == 1
        replacement = context.pages[0]
        assert replacement.url == "about:blank"
        assert not replacement.is_closed()
        assert tab.on_list(replacement)
        assert await tab.settle({"ok": True}) == {"ok": True}
        assert context.pages == [replacement], "settle must retain the guard's replacement too"

    asyncio.run(go())


@pytest.mark.parametrize("boundary", ["front", "goto", "title"])
@pytest.mark.parametrize("listener_before_return", [True, False])
def test_memory_close_during_navigation_keeps_the_lease_and_reports_navigation_failure(
    monkeypatch, navigation_errors, listener_before_return, boundary
) -> None:
    async def go() -> None:
        tab, context = await setup(listener_before_return)
        victim = context.pages[0]
        events: list[dict[str, Any]] = []

        async def close_target() -> None:
            assert await chromium.guard_memory(
                tab.browser.browser,
                90,
                lambda **event: events.append(event),
                lambda: (1006 << 20, 1024 << 20),
            )
            await context.drain()

        async def front() -> None:
            if victim.closed:
                raise navigation_errors.TargetClosedError("closed target")

        async def dwell() -> None:
            if boundary == "front":
                await close_target()

        async def goto(url, **kwargs):
            assert url == ON
            if boundary == "goto":
                await close_target()
                raise navigation_errors.TargetClosedError("closed target")

        async def title():
            await close_target()
            raise navigation_errors.TargetClosedError("closed target")

        monkeypatch.setattr(victim, "bring_to_front", front, raising=False)
        monkeypatch.setattr(victim, "goto", goto, raising=False)
        monkeypatch.setattr(victim, "title", title, raising=False)
        monkeypatch.setattr(tab.pacer, "dwell", dwell)
        result = await actions.run(tab, "browser_navigate", {"url": ON})
        assert result["error"] == "navigation_failed"
        assert victim.is_closed()
        assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1006}]
        assert not context.exit.done()
        assert len(context.pages) == 1
        assert context.pages[0].url == "about:blank"
        assert not context.pages[0].is_closed()

    asyncio.run(go())


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        "https://pages.example.evil.example/",
        "https://evilpages.example/",
        "https://pages.example@evil.example/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "about:Blank",
        "ABOUT:blank",
        "about:blank/",
        "about:blank?x=1",
        "about:blank#fragment",
        "about:blank ",
        " about:blank",
        "about:blanк",  # Cyrillic final letter.
        "about:blankevil",
        "about:srcdoc",
        "about://blank",
        "about:%62lank",
        "javascript:void(0)",
        "data:text/html,blank",
        "file:///tmp/blank",
        "chrome://newtab/",
        "",
    ],
)
@pytest.mark.parametrize("cleanup", ["listener", "settle"])
def test_forbidden_pages_still_close(url: str, cleanup: str) -> None:
    async def go() -> None:
        tab, context = await setup(False)
        assert policy.url_verdict(url, tab.hosts) is not None
        page = await context.new_page(url)
        if cleanup == "settle":
            await tab.settle({"ok": True})
        await context.drain()
        assert page.is_closed()
        assert context.pages == [tab.state.page]
        assert not context.exit.done()

    asyncio.run(go())


@pytest.mark.parametrize(
    "url", ["about:blank", ON, "http://pages.example/", "https://pages.example/a?b=c"]
)
@pytest.mark.parametrize("cleanup", ["listener", "settle"])
def test_allowed_pages_survive_listener_and_settle(url: str, cleanup: str) -> None:
    async def go() -> None:
        tab, context = await setup(False)
        page = await context.new_page(url)
        if cleanup == "settle":
            assert await tab.settle({"ok": True}) == {"ok": True}
            assert not page.is_closed()
        await context.drain()
        assert not page.is_closed()
        assert await tab.settle({"ok": True}) == {"ok": True}
        assert context.pages == [tab.state.page, page]
        # An internal blank is not an agent navigation capability.
        if url == "about:blank":
            assert policy.url_verdict(url, tab.hosts) == "refused_scheme"

    asyncio.run(go())
