"""Indeed's country sites (owner, 2026-10-28, jobs J18): "Follow its own country sites. A
redirect from www.indeed.com to any xx.indeed.com is followed, and the search continues there.
Redirects to any other domain stay refused."

The rule, in browserd (discovery.country_site, country_scope) and its copy in the api
(finder.country_site): only a search of the indeed platform (`Scope.country`), only from the
start page's first navigation, only https, only to the start page on a host of exactly two
lowercase letters plus `.indeed.com`. The frozen scope then moves to that host for the rest of
the session, once; every other rule holds there unchanged. Only the Playwright and CDP boundary
is fake here, and no address of Indeed is ever requested: test_b13_native.py proves it on real
Chromium with the fixture server under `www.indeed.test:8080` and `fr.indeed.test:8080`, the
stand ins of discovery.COUNTRY_HOMES (as platform_policy.test_start maps a platform there).
"""

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from tests.browser import test_b17_browserd as b17
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_b5_actions import Page, tab_of
from tests.browser.test_j18_scope import discovery
from tests.browser.test_multi_search import SearchSite, run, searching
from tests.browser.test_selected_interactive import Policy, selected

HOME = "https://www.indeed.com/"
FR = "https://fr.indeed.com/"

# --- the rule: the start page on one of the home's country sites ------------------------------

COUNTRY = [
    (HOME, FR),
    (HOME, "https://de.indeed.com/"),
    (HOME, "https://uk.indeed.com/"),
    ("https://www.indeed.com/jobs?q=robot", "https://fr.indeed.com/jobs?q=robot"),
    ("http://www.indeed.test:8080/b13/indeed", "http://fr.indeed.test:8080/b13/indeed"),
]
NOT_COUNTRY = [
    (HOME, HOME),  # the home itself: a redirect on its own host is a redirect_target
    (HOME, "http://fr.indeed.com/"),  # https only
    (HOME, "https://fra.indeed.com/"),  # exactly two letters
    (HOME, "https://f.indeed.com/"),
    (HOME, "https://f1.indeed.com/"),
    (HOME, "https://FR.indeed.com/"),  # as written, lower case
    (HOME, "https://fé.indeed.com/"),
    (HOME, "https://xn--fr.indeed.com/"),
    (HOME, "https://fr.fr.indeed.com/"),
    (HOME, "https://fr.indeed.com.example.test/"),  # another domain
    (HOME, "https://fr.indeed.co/"),
    (HOME, "https://fr.indeed.fr/"),
    (HOME, "https://fr.notindeed.com/"),
    (HOME, "https://frindeed.com/"),
    (HOME, "https://indeed.com/"),
    (HOME, "https://fr.indeed.com:8443/"),  # another port
    (HOME, "https://fr.indeed.com:443/"),
    (HOME, "https://user@fr.indeed.com/"),
    (HOME, "https://fr.indeed.com/apply"),  # another page than the start
    (HOME, "https://fr.indeed.com/emplois"),
    (HOME, "https://fr.indeed.com/?r=us"),
    (HOME, "https://fr.indeed.com/?"),
    (HOME, "https://fr.indeed.com/#top"),
    (HOME, "https://fr.indeed.com"),
    (HOME, "https://fr.indeed.com./"),
    (HOME, "https://elsewhere.example.test/"),
    (HOME, "javascript:alert(1)"),
    (HOME, ""),
    ("https://indeed.com/", "https://fr.indeed.com/"),  # the start is no home
    ("http://www.indeed.com/", "http://fr.indeed.com/"),
    ("https://www.indeed.com:8443/", "https://fr.indeed.com:8443/"),
    (FR, "https://de.indeed.com/"),  # a country site is no home: one move at most
    ("https://www.indeed.fr/", "https://fr.indeed.fr/"),
    ("https://www.linkedin.com/", "https://fr.linkedin.com/"),
    ("https://jobs.example.test/", "https://fr.example.test/"),
    ("http://www.indeed.test:8080/b13/indeed", "http://fr.indeed.test:8081/b13/indeed"),
    ("http://www.indeed.test/b13/indeed", "http://fr.indeed.test/b13/indeed"),
    (None, FR),
]


@pytest.mark.parametrize(("start", "url"), COUNTRY)
@pytest.mark.parametrize("module", [discovery, finder], ids=["browserd", "api"])
def test_the_start_page_on_a_country_site_of_its_home(module, start, url):
    assert module.country_site(start, url)


@pytest.mark.parametrize(("start", "url"), NOT_COUNTRY)
@pytest.mark.parametrize("module", [discovery, finder], ids=["browserd", "api"])
def test_anything_else_is_no_country_site(module, start, url):
    assert not module.country_site(start, url)


def test_the_two_copies_of_the_rule_are_identical():
    assert discovery.COUNTRY_HOMES == finder.COUNTRY_HOMES
    assert discovery.COUNTRY_LABEL.pattern == finder.COUNTRY_LABEL.pattern
    body = [inspect.getsource(m.country_site).split('"""')[2] for m in (discovery, finder)]
    assert body[0] == body[1]


def scope(start: str = HOME, *, country: bool = True, signed_in: bool = False):
    raw = {"start": start, "titles": ["robot"], "places": ["Paris"], "filters": {}}
    return discovery.Scope.parse(
        {
            **raw,
            **({"country": True} if country else {}),
            **({"signed_in": True} if signed_in else {}),
        }
    )


def test_the_scope_moves_once_and_keeps_every_other_rule():
    moved = discovery.country_scope(scope(), FR)
    assert moved is not None
    assert (moved.start, moved.country, moved.titles, moved.places) == (
        FR,
        False,
        ("robot",),
        ("Paris",),
    )
    assert discovery.country_scope(moved, "https://de.indeed.com/") is None
    assert discovery.read_url("https://fr.indeed.com/jobs?q=robot&l=", FR, scope=moved) is False
    assert discovery.read_url("https://fr.indeed.com/jobs?q=robot", FR, scope=moved)
    assert discovery.read_url("https://fr.indeed.com/jobs?q=robot&location=Paris", FR, scope=moved)
    for refused in (
        "https://fr.indeed.com/jobs?q=other",
        "https://fr.indeed.com/apply",
        "https://fr.indeed.com/account/login",
        "https://fr.indeed.com/jobs/42/candidature",
        "https://www.indeed.com/jobs?q=robot",  # the home is left behind
        "https://de.indeed.com/jobs?q=robot",
    ):
        assert not discovery.read_url(refused, FR, scope=moved), refused


def test_a_scope_of_another_platform_never_moves():
    assert discovery.country_scope(scope(country=False), FR) is None


@pytest.mark.parametrize("country", [1, "yes", None, [], "true"])
def test_a_country_that_is_not_a_boolean_refuses_the_scope(country):
    raw = {"start": HOME, "titles": ["robot"], "places": [], "filters": {}, "country": country}
    assert discovery.Scope.parse(raw) is None


# --- browserd, anonymous discovery: the redirect of the first navigation ----------------------


def tab(start_scope, page_url: str = "about:blank"):
    page = Page(page_url)
    found = tab_of(page)
    found.request = SimpleNamespace(hosts=("www.indeed.com",), purpose="sweep")
    found.state.scope = found.state.given = start_scope
    found.state.discovery = True
    found.state.native_context = found.browser.browser.contexts[0]
    sent: list[Any] = []

    async def send(name, args):
        sent.append(name)

    found.state.native_cdp = SimpleNamespace(send=send)
    return found, sent


def paused(found, url: str, status: int | None = None, location: str | None = None) -> None:
    event: dict[str, Any] = {"requestId": "one", "request": {"method": "GET", "url": url}}
    if status is not None:
        event["responseStatusCode"] = status
        event["responseHeaders"] = (
            [] if location is None else [{"name": "Location", "value": location}]
        )
    asyncio.run(found.intercept(event))


def test_the_home_s_redirect_to_a_country_site_is_followed_and_the_search_continues_there():
    found, sent = tab(scope())
    paused(found, HOME, 302, FR)
    assert sent == ["Fetch.continueRequest"]
    assert (found.state.scope.start, found.state.country) == (FR, "fr.indeed.com")
    assert found.state.given == scope()  # every later call of the api still carries its scope
    paused(found, FR)  # Chromium's request to the country site, at the Request stage
    paused(found, "https://fr.indeed.com/jobs?q=robot")
    assert sent == ["Fetch.continueRequest"] * 3
    assert not found.state.blocked
    assert found.allowed("https://fr.indeed.com/jobs?q=robot")
    assert not found.allowed("https://de.indeed.com/")
    paused(found, "https://fr.indeed.com/apply")
    assert sent[-1] == "Fetch.failRequest"


# urljoin drops an empty query: `https://fr.indeed.com/?` is the start page itself, followed.
@pytest.mark.parametrize(
    ("start", "location"),
    [p for p in NOT_COUNTRY if p[0] == HOME and p[1] != "https://fr.indeed.com/?"],
)
def test_a_redirect_to_anything_else_is_refused(start, location):
    found, sent = tab(scope(start))
    paused(found, start, 302, location)
    if location in {HOME}:  # its own host, the start itself: the ordinary rule
        assert sent == ["Fetch.continueRequest"]
    else:
        assert sent == ["Fetch.failRequest"]
        assert found.state.blocked
    assert (found.state.scope.start, found.state.country) == (start, "")


def test_another_platform_s_redirect_to_a_country_site_is_refused():
    found, sent = tab(scope(country=False))
    paused(found, HOME, 302, FR)
    assert sent == ["Fetch.failRequest"]
    assert found.state.country == ""


def test_a_redirect_after_the_first_page_is_refused():
    found, sent = tab(scope(), page_url=HOME)
    paused(found, "https://www.indeed.com/jobs?q=robot", 302, "https://fr.indeed.com/jobs?q=robot")
    assert sent == ["Fetch.failRequest"]
    assert found.state.country == ""


def test_a_second_country_redirect_is_refused():
    found, sent = tab(scope())
    paused(found, HOME, 302, FR)
    paused(found, FR, 302, "https://de.indeed.com/")
    assert sent == ["Fetch.continueRequest", "Fetch.failRequest"]
    assert found.state.scope.start == FR


@pytest.mark.parametrize("location", ["/\\fr.indeed.com/", "https://fr.indeed.com/\t", " " + FR])
def test_a_country_location_chromium_reads_otherwise_is_refused(location):
    found, sent = tab(scope())
    paused(found, HOME, 302, location)
    assert sent == ["Fetch.failRequest"]


def test_the_api_s_own_scope_is_still_taken_after_the_move_and_a_moved_one_is_not():
    found, _ = tab(scope())
    paused(found, HOME, 302, FR)
    asyncio.run(found.begin_discovery(scope(), "browser_snapshot", {}))
    with pytest.raises(b17.actions.UnsupportedDiscovery):
        asyncio.run(found.begin_discovery(found.state.scope, "browser_snapshot", {}))


# --- browserd, signed in discovery: the redirect hop of the first navigation ------------------


def hop(start_scope, url: str, *, page_url: str = b17.actions.ABOUT_BLANK):
    context = b17.Context([b17.Page()])
    found = b17.tab_on(context)
    found.request = SimpleNamespace(hosts=("www.indeed.com",), purpose="sweep")
    asyncio.run(found.begin_discovery(start_scope, "browser_navigate", {"url": start_scope.start}))
    found.state.page.url = page_url  # the first page loaded, or still blank
    stopped: list[Any] = []

    async def stop(frame):
        stopped.append(frame)

    request = SimpleNamespace(
        url=url,
        method="GET",
        redirected_from=SimpleNamespace(url=start_scope.start),
        is_navigation_request=lambda: True,
        frame=SimpleNamespace(parent_frame=None),
    )

    async def go():
        original = b17.actions.stop_loading
        b17.actions.stop_loading = stop
        try:
            found._hop(request)
            await asyncio.sleep(0)
        finally:
            b17.actions.stop_loading = original

    asyncio.run(go())
    return found, stopped


def test_a_signed_in_home_s_redirect_to_a_country_site_is_followed():
    found, stopped = hop(scope(signed_in=True), FR)
    assert not stopped
    assert not found.state.blocked
    assert (found.state.scope.start, found.state.country) == (FR, "fr.indeed.com")
    assert discovery.signed_request(
        "https://fr.indeed.com/jobs?q=robot", "GET", True, found.state.scope
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://fra.indeed.com/",
        "http://fr.indeed.com/",
        "https://fr.indeed.com/apply",
        "https://elsewhere.example.test/",
    ],
)
def test_a_signed_in_redirect_anywhere_else_is_stopped(url):
    found, stopped = hop(scope(signed_in=True), url)
    assert len(stopped) == 1
    assert found.state.blocked
    assert found.state.country == ""


def test_a_signed_in_country_redirect_after_the_first_page_is_stopped():
    found, stopped = hop(scope(signed_in=True), FR, page_url=HOME)
    assert len(stopped) == 1
    assert found.state.country == ""


def test_a_signed_in_redirect_of_another_platform_is_stopped():
    _, stopped = hop(scope(signed_in=True, country=False), FR)
    assert len(stopped) == 1


# --- the api: the search continues on the country site ----------------------------------------


class Country(SearchSite):
    """Indeed's home answering on the person's country site, `landing`, where it then runs."""

    def __init__(self, landing: str, **kw: Any) -> None:
        super().__init__(base=HOME, **kw)
        self.landing = landing

    async def call(self, name, args):
        answer = await super().call(name, args)
        if name == "browser_navigate" and self.current == HOME:
            self.base = self.current = self.landing
            self.pages[self.landing] = self.pages[HOME]
        return answer


def test_an_indeed_search_landing_on_its_country_site_runs_there():
    site = Country(FR, results={"robot": ["Robot Engineer"]})
    seen: list[finder.FinderSession] = []
    entry, _, leases = run(
        selected(url=HOME, title_include={"terms": ["robot"]}),
        site,
        ScriptedFinder(searching(site, log=seen)),
        policy=Policy(),
        sweep_id="00000000-0000-4000-8000-000000000718",
    )
    assert entry["error"] == "finding_persistence_pending", entry
    assert (entry["searches"], entry["found"]) == (1, 1)
    [session] = seen
    assert session.landed == FR
    assert session.site == ("https", "fr.indeed.com")
    assert session.on_site("https://fr.indeed.com/jobs/robot-engineer")
    assert not session.on_site("https://www.indeed.com/jobs/robot-engineer")
    assert site.scopes[0]["country"] is True
    assert leases.acquired[0][-1] == ["www.indeed.com"]  # the lease opens the home alone


@pytest.mark.parametrize(
    "landing",
    ["https://fra.indeed.com/", "https://fr.indeed.com/emplois", "https://elsewhere.example.test/"],
)
def test_an_indeed_search_landing_anywhere_else_never_starts_the_agent(landing):
    site = Country(landing, results={"robot": ["Robot Engineer"]})
    started: list[Any] = []

    async def agent(session):
        started.append(session)
        return finder.Ran()

    entry, stored, _ = run(
        selected(url=HOME, title_include={"terms": ["robot"]}),
        site,
        ScriptedFinder(agent),
        policy=Policy(),
        sweep_id="00000000-0000-4000-8000-000000000718",
    )
    assert entry["error"] in {"page_failed", "unsupported_interaction"}
    assert not started
    assert not stored


def test_another_platform_landing_on_a_country_like_host_never_starts_the_agent():
    start = "https://www.example-jobs.test/"
    site = SearchSite(base=start, results={"robot": ["Robot Engineer"]})
    entry, _, _ = run(selected(url=start), site, ScriptedFinder(searching(site)))
    assert "country" not in site.scopes[0]
    assert not finder.country_site(start, "https://fr.example-jobs.test/")
    assert entry["searches"] == 1


def test_a_session_refuses_a_country_landing_without_the_country_scope():
    plain = finder.InteractiveScope(HOME, ("robot",))
    with pytest.raises(ValueError, match="another origin"):
        finder.FinderSession(None, HOME, "", "e2e-j18", interactive=plain, landed=FR)
    country = finder.InteractiveScope(HOME, ("robot",), country=True)
    session = finder.FinderSession(None, HOME, "", "e2e-j18", interactive=country, landed=FR)
    assert session.site == ("https", "fr.indeed.com")
    assert country.tool_scope()["country"] is True
    assert "country" not in plain.tool_scope()
