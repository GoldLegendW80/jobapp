"""A job website's start page that redirects on its own host is followed (jobs J18, issue 716).

Welcome to the Jungle answers its home page `/` with a redirect to `/en`; every signed in search
of it ended `page_failed` on its first page: the redirect hop was judged against the tab's
address, still `about:blank` while the first navigation runs, so browserd stopped it and answered
`host_not_allowed`. The anonymous discovery refused every redirect response outright. A redirect
hop is now judged by the rule of the navigation it replaces (the frozen scope: same scheme, host
and port as the start, no apply, submit, account or sign out address, only the saved criteria in
its query), so a locale prefix or a trailing slash is followed and every other hop is still
stopped. The api accepts a first page on the start's own origin and logs the failure code of a
page that did not load (the code only, never page text nor an address).

Only the Playwright and CDP boundary is fake here; test_b13_native.py proves the anonymous case on
real Chromium with the fixture pages server (`/b13/locale`).
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from tests.browser import test_b17_browserd as b17
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_b5_actions import Page, tab_of
from tests.browser.test_b13_discovery import bind_native
from tests.browser.test_multi_search import SearchSite, run, searching
from tests.browser.test_selected_interactive import selected

# --- browserd, anonymous discovery: a redirect response of the scope's own host is followed ----


def intercepted(status: int, location: str | None, url: str = "http://pages/") -> list[Any]:
    tab = tab_of(Page())
    bind_native(tab)
    commands: list[Any] = []

    async def send(name, args):
        commands.append((name, args))

    tab.state.native_cdp = SimpleNamespace(send=send)
    headers = [{"name": "Set-Cookie", "value": "synthetic=1"}]
    if location is not None:
        headers.append({"name": "Location", "value": location})
    event = {
        "requestId": "one",
        "request": {"method": "GET", "url": url},
        "responseStatusCode": status,
        "responseHeaders": headers,
    }
    asyncio.run(tab.intercept(event))
    return [commands, tab.state.blocked]


@pytest.mark.parametrize(
    ("status", "location"),
    [
        (301, "/en"),
        (302, "/en/"),
        (307, "http://pages/fr"),
        (303, "en"),
    ],
)
def test_a_redirect_on_the_scope_s_own_host_is_followed(status, location):
    commands, blocked = intercepted(status, location)
    assert commands == [("Fetch.continueRequest", {"requestId": "one"})]
    assert not blocked


@pytest.mark.parametrize(
    ("status", "location"),
    [
        (302, None),  # no target: nothing to judge
        (302, ""),
        (302, "http://elsewhere/en"),  # another host
        (302, "https://pages/en"),  # another scheme
        (302, "http://pages:8443/en"),  # another port
        (302, "//elsewhere/en"),
        (302, "/apply"),
        (302, "/jobs/42/candidature"),
        (302, "/account/settings"),
        (302, "/login?next=/en"),
        (302, "/logout"),
        (302, "/remove"),
        (302, "/en?q=other"),  # a title never saved
        (302, "/en?lang=en"),  # a key no search takes
        (302, "/en#top"),
        (308, "/en?page=2"),  # a query without the saved title is no search
        (302, "/en/jobs"),  # a search address without the saved criteria, as for a link
        (302, "/%61pply"),
        (302, "http://user:pass@pages/en"),
        (302, "javascript:alert(1)"),
        (302, "data:text/html,x"),
        (302, "/" + "a" * 2100),
        (304, "/en"),  # not a redirect the browser follows
        # urljoin keeps these on the host; Chromium reads a backslash as a slash and leaves
        (302, "/\\elsewhere/en"),
        (302, "\\\\elsewhere/en"),
        (302, "/\\pages:8080/en"),
        (302, "/en\\"),
        (302, "/%5Celsewhere/en"),  # percent encoded
        (302, "/%5celsewhere/en"),
        # Chromium strips a tab or a newline and keeps a space; none is read alike by urljoin
        (302, "/\t/elsewhere/en"),
        (302, "/\n/elsewhere/en"),
        (302, "/\r\n/elsewhere/en"),
        (302, " /en"),
        (302, "/en "),
        (302, "/e n"),
        (302, "/\x00en"),
        (302, "/en\x7f"),
        (302, "/en\u0085"),
        (302, "/%09/elsewhere/en"),  # percent encoded
        (302, "/%0a/elsewhere/en"),
        (302, "/en%20x"),
        (302, "/en?q=robot%20engineer"),
        (302, "/en%C2%85"),
    ],
)
def test_a_redirect_anywhere_else_is_still_refused(status, location):
    commands, blocked = intercepted(status, location)
    assert commands == [
        ("Fetch.failRequest", {"requestId": "one", "errorReason": "BlockedByClient"})
    ]
    assert blocked


def test_a_redirect_of_a_refused_request_is_refused():
    commands, blocked = intercepted(302, "/en", url="http://elsewhere/")
    assert commands[0][0] == "Fetch.failRequest"
    assert blocked


# --- browserd, signed in discovery: the redirect hop of the first navigation is followed -------


def hop(url: str, *, page_url: str = b17.actions.ABOUT_BLANK, main: bool = True) -> Any:
    context = b17.Context([b17.Page(page_url)])
    tab = b17.begin(context)
    stopped: list[Any] = []

    async def stop(frame):
        stopped.append(frame)

    request = SimpleNamespace(
        url=url,
        method="GET",
        redirected_from=SimpleNamespace(url=b17.START),
        is_navigation_request=lambda: True,
        frame=SimpleNamespace(parent_frame=None if main else SimpleNamespace()),
    )

    async def go():
        original = b17.actions.stop_loading
        b17.actions.stop_loading = stop
        try:
            tab._hop(request)
            await asyncio.sleep(0)
        finally:
            b17.actions.stop_loading = original

    asyncio.run(go())
    return tab, stopped


START_HOST = "https://jobs.example.test"


@pytest.mark.parametrize(
    "url",
    [
        f"{START_HOST}/en",
        f"{START_HOST}/en/",
        f"{START_HOST}/fr/",
        f"{START_HOST}/en/search?q=robot",
    ],
)
def test_a_signed_in_start_page_redirect_on_its_host_is_followed(url):
    tab, stopped = hop(url)
    assert not tab.state.blocked
    assert not stopped


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.example.test/en",
        "http://jobs.example.test/en",
        f"{START_HOST}/login?next=/en",
        f"{START_HOST}/en/logout",
        f"{START_HOST}/jobs/42/apply",
        f"{START_HOST}/en/account",
        f"{START_HOST}/en?q=other",
        f"{START_HOST}/en?lang=fr",
        f"{START_HOST}/en?action=apply",
        f"{START_HOST}/fr/search",  # a search address without the saved criteria
    ],
)
def test_a_signed_in_redirect_anywhere_else_is_still_stopped(url):
    tab, stopped = hop(url)
    assert tab.state.blocked
    assert len(stopped) == 1


def test_a_signed_in_sign_in_redirect_is_told():
    tab, _ = hop(f"{START_HOST}/signin")
    assert tab.state.signin_seen


def test_a_sub_frame_redirect_is_left_to_the_route():
    tab, stopped = hop("https://ads.example.net/frame", main=False)
    assert not tab.state.blocked
    assert not stopped


# --- the api: a first page on the start's own origin starts the agent --------------------------


class Redirecting(SearchSite):
    """A website whose start page answers on another address: `landing` (its locale path)."""

    def __init__(self, landing: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.landing = landing

    async def call(self, name, args):
        answer = await super().call(name, args)
        if name == "browser_navigate" and self.current == self.base:
            self.pages[self.landing] = self.pages[self.base]
            self.current = self.landing
        return answer


def test_a_start_page_redirected_on_its_own_origin_starts_the_agent():
    site = Redirecting(f"{SearchSite.base}en", results={"robot": ["Robot Engineer"]})
    entry, _, _ = run(
        selected(title_include={"terms": ["robot"]}), site, ScriptedFinder(searching(site))
    )
    # The search ran and its posting was found (stored later, as for every website, J18).
    assert entry["error"] == "finding_persistence_pending", entry
    assert (entry["searches"], entry["found"]) == (1, 1)


@pytest.mark.parametrize(
    "landing",
    [
        "https://elsewhere.example.test/en",
        SearchSite.base.replace("https://", "http://") + "en",
        SearchSite.base.rstrip("/") + ":8443/en",
    ],
)
def test_a_start_page_landing_on_another_origin_never_starts_the_agent(landing):
    site = Redirecting(landing, results={"robot": ["Robot Engineer"]})
    started: list[Any] = []

    async def agent(session: finder.FinderSession) -> finder.Ran:
        started.append(session)
        return finder.Ran()

    entry, _, _ = run(selected(title_include={"terms": ["robot"]}), site, ScriptedFinder(agent))
    # The tools' own host check (page_failed) or _search's origin check: the agent never starts.
    assert entry["error"] in {"page_failed", "unsupported_interaction"}
    assert not started


class Failing(SearchSite):
    def __init__(self, error: Any, **kw: Any) -> None:
        super().__init__(**kw)
        self.error = error

    async def call(self, name, args):
        await super().call(name, dict(args))
        return {
            "ok": False,
            "error": self.error,
            "message": "Planted page words 7f3a9c",
            "url": f"{self.base}en?token=7f3a9c",
        }


@pytest.mark.parametrize(
    ("error", "logged"),
    [
        ("host_not_allowed", "host_not_allowed"),
        ("navigation_failed", "navigation_failed"),
        ("signin_required", "signin_required"),
        ("Planted page words 7f3a9c", "other"),
        (None, "other"),
        (["host_not_allowed"], "other"),
        ("x" * 80, "other"),
    ],
)
def test_the_failure_code_of_a_first_page_is_logged_and_nothing_else(caplog, error, logged):
    site = Failing(error)
    with caplog.at_level(logging.INFO, logger="app.browser.sources"):
        entry, _, _ = run(
            selected(title_include={"terms": ["robot"]}), site, ScriptedFinder(searching(site))
        )
    assert entry["error"] == "page_failed"
    lines = [r.getMessage() for r in caplog.records if r.name == "app.browser.sources"]
    assert any(f"error={logged}" in line and "browser_navigate" in line for line in lines), lines
    assert not any("7f3a9c" in line for line in lines)
