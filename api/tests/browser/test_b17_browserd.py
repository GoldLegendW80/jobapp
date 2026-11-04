"""B17 in browserd: the signed in discovery context of a website the person signed in to.

Only the Playwright and CDP boundary is fake (no Chromium here; the journey
web/e2e/browser-b17.spec.ts runs the real one). Before the first navigation the tab bypasses
every service worker, asks Chromium to block WebSockets and gets the script that removes workers and
`navigator.serviceWorker`; any step that
fails ends the discovery with nothing read. The route lets a request leave only by
`discovery.signed_request`, and a tab sent to a sign in page is told `signin_required`.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import chromium  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import Request  # noqa: E402
from browserd.tools import actions, discovery, pacing  # noqa: E402

START = "https://jobs.example.test/search"
SCOPE = discovery.Scope.parse({"start": START, "signed_in": True})


class Cdp:
    def __init__(self, fail: str | None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.fail = fail

    async def send(self, method: str, params: Any = None) -> dict[str, Any]:
        if method == self.fail:
            raise RuntimeError("Protocol error")
        self.sent.append((method, params))
        return {}


class Page:
    def __init__(self, url: str = actions.ABOUT_BLANK) -> None:
        self.url, self.closed = url, False
        self.context: Any = None

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self, pages: list[Page], fail: str | None = None) -> None:
        self.pages = pages
        for page in pages:
            page.context = self  # what Tab._watch opens its CDP session on (B20)
        self.order: list[str] = []
        self.routes: list[Any] = []
        self.scripts: list[str] = []
        self.listeners: dict[str, list[Any]] = {}
        self.cdp = Cdp(fail)
        self.closed = False

    async def add_init_script(self, script: str) -> None:
        self.order.append("script")
        self.scripts.append(script)

    async def route(self, pattern: str, handler: Any) -> None:
        self.order.append("route")
        self.routes.append((pattern, handler))

    def on(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def new_cdp_session(self, page: Page) -> Cdp:
        self.order.append("cdp")
        return self.cdp

    async def close(self) -> None:  # the person's own context is never closed by a search
        self.closed = True


def tab_on(context: Context) -> actions.Tab:
    request = Request.parse(
        {
            "user_id": "e2e-b17",
            "profile_id": "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b17",
            "uid": 20017,
            "purpose": "sweep",
            "hosts": ["jobs.example.test"],
        }
    )
    browser = chromium.Chromium(request, "b17-test", None, Config(secret=b"s"))
    browser.browser = SimpleNamespace(contexts=[context])

    async def no_sleep(_: float) -> None:
        raise AssertionError("no pacing here")

    return actions.Tab(
        request=request,
        state=actions.State(),
        browser=browser,
        pacer=pacing.Pacer(no_sleep),
        lease_tag="b17-test",
    )


def begin(context: Context) -> actions.Tab:
    tab = tab_on(context)
    asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    return tab


def test_the_persons_context_bypasses_service_workers_before_the_first_navigation() -> None:
    """B20 (owner, 2026-11-04): the signed in context loses SIGNED_IN_JS (no worker, service
    worker or socket restriction); the route of the line is set and service workers bypassed,
    so every request of the page reaches that route, before the first navigation."""
    first, other = Page(), Page()
    context = Context([first, other])
    tab = begin(context)
    assert tab.state.signed_in
    assert tab.state.discovery
    assert tab.state.native_context is context
    assert tab.state.page is first
    assert other.closed
    assert not first.closed
    assert not context.closed
    assert context.scripts == []
    assert context.order == ["route", "cdp"]
    assert context.cdp.sent == [
        ("Network.enable", None),
        ("Network.setBypassServiceWorker", {"bypass": True}),
    ]
    assert not hasattr(actions, "SIGNED_IN_JS")


@pytest.mark.parametrize("fail", ["Network.enable", "Network.setBypassServiceWorker"])
def test_a_hardening_step_that_fails_ends_the_discovery(fail: str) -> None:
    page = Page()
    context = Context([page], fail=fail)
    tab = tab_on(context)
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    assert tab.state.native_failed
    assert page.closed  # the tab only, never the person's context
    assert not context.closed


def test_a_page_already_open_is_never_upgraded() -> None:
    context = Context([Page("https://jobs.example.test/me")])
    tab = tab_on(context)
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    assert context.routes == []
    assert context.cdp.sent == []


class Route:
    def __init__(self) -> None:
        self.outcome: str | None = None

    async def fallback(self) -> None:
        self.outcome = "sent"

    async def abort(self, reason: str) -> None:
        self.outcome = reason


def request(url: str, method: str = "GET", frame: str | None = None) -> Any:
    """A request: `frame` "main" or "sub" for a document, None for anything else."""
    parent = None if frame == "main" else SimpleNamespace()
    return SimpleNamespace(
        url=url,
        method=method,
        resource_type="document" if frame is not None else "script",
        is_navigation_request=lambda: frame is not None,
        frame=SimpleNamespace(parent_frame=parent),
    )


def test_nothing_leaves_after_the_end_of_the_discovery() -> None:
    """The route of the line (B20) refuses every request once the discovery ended, a read
    included."""
    tab = begin(Context([Page()]))
    asyncio.run(tab.close_discovery())
    for url in (f"{START}?q=robot", "https://cdn.example.net/app.js"):
        route = Route()
        asyncio.run(tab._line_route(route, request(url)))
        assert route.outcome == "blockedbyclient"


def test_an_anonymous_scope_never_touches_the_persons_context() -> None:
    """B20: the anonymous context is a normal one, JavaScript on and service workers allowed,
    and never the person's own."""
    page = Page()
    context = Context([page])
    tab = tab_on(context)
    anonymous = discovery.Scope.parse({"start": START})
    made: list[dict[str, Any]] = []

    async def new_context(**options: Any) -> Any:
        made.append(options)
        raise RuntimeError("no Chromium here")

    tab.browser.browser.new_context = new_context
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(anonymous, "browser_navigate", {"url": START}))
    assert made == [
        {"java_script_enabled": True, "service_workers": "allow", "accept_downloads": False}
    ]
    assert context.routes == []
    assert context.scripts == []
    assert not tab.state.signed_in
