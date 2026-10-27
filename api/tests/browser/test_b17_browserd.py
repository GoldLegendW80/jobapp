"""B17 in browserd: the signed in discovery context of a website the person signed in to.

Only the Playwright and CDP boundary is fake (no Chromium here; the journey
web/e2e/browser-b17.spec.ts runs the real one). Before the first navigation the tab bypasses
every service worker, blocks WebSockets and gets the script that removes workers; any step that
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
SCOPE = discovery.Scope.parse({"start": START, "query": "robot", "filters": {}, "signed_in": True})


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

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self, pages: list[Page], fail: str | None = None) -> None:
        self.pages = pages
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


def test_the_persons_context_bypasses_service_workers_and_blocks_sockets_first() -> None:
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
    assert context.scripts == [actions.SIGNED_IN_JS]
    assert context.order == ["script", "route", "cdp"]  # all before the first navigation
    assert context.cdp.sent == [
        ("Network.enable", None),
        ("Network.setBypassServiceWorker", {"bypass": True}),
        ("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]}),
    ]
    for name in ("Worker", "SharedWorker", "WebSocket", "WebTransport", '"register"'):
        assert name in actions.SIGNED_IN_JS


@pytest.mark.parametrize(
    "fail",
    ["Network.enable", "Network.setBypassServiceWorker", "Network.setBlockedURLs"],
)
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
        is_navigation_request=lambda: frame is not None,
        frame=SimpleNamespace(parent_frame=parent),
    )


@pytest.mark.parametrize(
    ("url", "method", "frame", "sent"),
    [
        (f"{START}?q=robot", "GET", "main", True),
        ("https://jobs.example.test/jobs/42", "GET", "main", True),
        ("https://jobs.example.test/jobs/42", "HEAD", "main", True),
        (f"{START}?q=robot", "POST", "main", False),
        ("https://jobs.example.test/jobs/42/apply", "GET", "main", False),
        ("https://jobs.example.test/login?next=/search", "GET", "main", False),
        ("https://elsewhere.example.test/search?q=robot", "GET", "main", False),
        ("https://jobs.example.test/embed/other", "GET", "sub", True),
        ("https://ads.example.net/frame", "GET", "sub", False),
        ("https://cdn.example.net/app.js", "GET", None, True),
        ("https://jobs.example.test/api/jobs?q=robot", "GET", None, True),
        ("https://jobs.example.test/api/jobs/42/save", "POST", None, False),
        ("https://jobs.example.test/api/jobs/42/easy-apply", "GET", None, False),
        ("https://jobs.example.test/logout", "GET", None, False),
        ("https://jobs.example.test/api/session?action=signout", "GET", None, False),
        ("https://jobs.example.test/api/messages", "PUT", None, False),
        ("https://jobs.example.test/api/beacon", "POST", None, False),
    ],
)
def test_the_route_lets_only_reads_leave(url: str, method: str, frame: str | None, sent: bool):
    tab = begin(Context([Page()]))
    route = Route()
    asyncio.run(tab._signed_route(route, request(url, method, frame)))
    assert route.outcome == ("sent" if sent else "blockedbyclient")


def test_a_sign_in_page_is_told_and_nothing_leaves_after_the_end() -> None:
    tab = begin(Context([Page()]))
    route = Route()
    asyncio.run(tab._signed_route(route, request("https://jobs.example.test/login", "GET", "main")))
    assert route.outcome == "blockedbyclient"
    assert tab.state.signin_seen
    assert tab.state.blocked
    asyncio.run(tab.close_discovery())
    for url in (f"{START}?q=robot", "https://cdn.example.net/app.js"):
        route = Route()
        asyncio.run(tab._signed_route(route, request(url)))
        assert route.outcome == "blockedbyclient"


def test_an_anonymous_scope_never_touches_the_persons_context() -> None:
    page = Page()
    context = Context([page])
    tab = tab_on(context)
    anonymous = discovery.Scope.parse({"start": START, "query": "robot", "filters": {}})
    made: list[dict[str, Any]] = []

    async def new_context(**options: Any) -> Any:
        made.append(options)
        raise RuntimeError("no Chromium here")

    tab.browser.browser.new_context = new_context
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(anonymous, "browser_navigate", {"url": START}))
    assert made == [
        {"java_script_enabled": False, "service_workers": "block", "accept_downloads": False}
    ]
    assert context.routes == []
    assert context.scripts == []
    assert not tab.state.signed_in


# What a signed in page may ask without the person: never to sign them out (path or query, any
# spelling), while the reads of a search, their account data and words that only contain the
# letters stay allowed.
@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("https://jobs.example.test/logout", True),
        ("https://jobs.example.test/LogOut", True),
        ("https://jobs.example.test/log-out", True),
        ("https://jobs.example.test/log_out?next=/", True),
        ("https://jobs.example.test/api/v2/sign-out", True),
        ("https://jobs.example.test/users/sign_out", True),
        ("https://jobs.example.test/SignOut.aspx", True),
        ("https://jobs.example.test/logoff", True),
        ("https://jobs.example.test/fr/deconnexion", True),
        ("https://jobs.example.test/fr/se-deconnecter", True),
        ("https://jobs.example.test/de/abmelden", True),
        ("https://jobs.example.test/account/disconnect", True),
        ("https://jobs.example.test/index.php?action=logout", True),
        ("https://jobs.example.test/a?do=sign-out", True),
        ("https://jobs.example.test/a?logout", True),
        ("https://jobs.example.test/a?next=%2Flogout", True),
        ("https://jobs.example.test/jobs/42/apply", True),
        ("https://jobs.example.test/api/jobs/42/save", True),
        ("https://jobs.example.test/api/alerts/7/delete", True),
        ("https://jobs.example.test/checkout", True),
        ("https://jobs.example.test/jobs/42/candidater", True),
        ("https://jobs.example.test/search?q=robot", False),
        ("https://jobs.example.test/api/me", False),
        ("https://jobs.example.test/api/account", False),
        ("https://jobs.example.test/jobs/logistics-engineer", False),
        ("https://jobs.example.test/jobs/log-analyst", False),
        ("https://jobs.example.test/jobs/sign-language-teacher", False),
        ("https://jobs.example.test/blog/out-of-office", False),
        ("https://jobs.example.test/connexions-reseau", False),
        ("https://jobs.example.test/jobs?q=signal+outage", False),
        ("https://jobs.example.test/jobs?location=Logrono", False),
        ("https://cdn.example.net/static/app.4f2a.js", False),
        ("https://jobs.example.test/jobs/supply-chain-lead", False),
        ("https://jobs.example.test/jobs/applied-scientist", False),
    ],
)
def test_a_signed_in_request_never_signs_the_person_out(url: str, refused: bool) -> None:
    assert discovery.destructive(url) is refused


@pytest.mark.parametrize(
    ("url", "signin"),
    [
        ("https://jobs.example.test/login", True),
        ("https://jobs.example.test/log-in", True),
        ("https://jobs.example.test/users/sign_in", True),
        ("https://jobs.example.test/SignIn?ReturnUrl=%2Fjobs", True),
        ("https://jobs.example.test/logon.aspx", True),
        ("https://jobs.example.test/fr/connexion", True),
        ("https://jobs.example.test/fr/se-connecter", True),
        ("https://sso.example.test/sso/start", True),
        ("https://auth.example.test/auth/realms/x", True),
        ("https://jobs.example.test/search?q=login", False),
        ("https://jobs.example.test/jobs/42", False),
        ("https://jobs.example.test/jobs/logistics", False),
        ("https://jobs.example.test/signals", False),
        ("https://jobs.example.test/", False),
        ("", False),
    ],
)
def test_the_sign_in_address_is_read_from_the_path(url: str, signin: bool) -> None:
    assert discovery.signin_url(url) is signin
