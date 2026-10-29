"""A job website's hosts are its platform's family (owner, 2026-10-29, B19: "Let it use the
search boxes ... each site's own hosts and its job links are allowed"), replacing the one country
move of 2026-10-28: Indeed `indeed.com`, `www.indeed.com` and every `<two letters>.indeed.com`;
LinkedIn the same under `linkedin.com`; Welcome to the Jungle `welcometothejungle.com` and its
`www.` host; https and the default port only. A redirect within the family is followed, one to
the platform's sign in host is the sign in wall, one anywhere else stays stopped.

The rule, in browserd (discovery.FAMILIES, family_of, on_site) and its copy in the api
(finder.FAMILIES, family_of, on_family, lease_hosts). Only the Playwright and CDP boundary is
fake, and no address of a real platform is ever requested: its `.test` stand ins (http, port
8080) are the fixture server's names.
"""

import asyncio
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

# --- the rule: an address on the start's family -----------------------------------------------

ON = [
    (HOME, FR),
    (HOME, "https://de.indeed.com/jobs?q=robot"),
    (HOME, "https://uk.indeed.com/viewjob?jk=abc"),
    (HOME, "https://indeed.com/"),
    (HOME, "https://www.indeed.com/jobs"),
    (HOME, "https://WWW.Indeed.COM/"),  # a host is read in lower case, as Chromium does
    (HOME, "https://fr.indeed.com:443/"),
    (FR, "https://de.indeed.com/"),  # any country site, at any time
    ("https://indeed.com/", "https://fr.indeed.com/"),
    ("https://www.linkedin.com/", "https://linkedin.com/jobs/"),
    ("https://www.linkedin.com/", "https://fr.linkedin.com/jobs/view/123/"),
    ("https://linkedin.com/", "https://www.linkedin.com/feed/"),
    ("https://www.welcometothejungle.com/en/jobs", "https://welcometothejungle.com/fr/jobs"),
    ("http://www.indeed.test:8080/b19/", "http://fr.indeed.test:8080/b19/"),
    ("http://www.welcometothejungle.test:8080/", "http://welcometothejungle.test:8080/"),
    ("https://jobs.example.test/", "https://jobs.example.test/search?q=robot"),  # no family
]
OFF = [
    (HOME, "http://fr.indeed.com/"),  # https only
    (HOME, "https://fra.indeed.com/"),  # exactly two letters
    (HOME, "https://f.indeed.com/"),
    (HOME, "https://f1.indeed.com/"),
    (HOME, "https://fé.indeed.com/"),
    (HOME, "https://xn--fr.indeed.com/"),
    (HOME, "https://fr.fr.indeed.com/"),
    (HOME, "https://secure.indeed.com/auth"),  # the sign in host is no page of the family
    (HOME, "https://employers.indeed.com/"),
    (HOME, "https://apis.indeed.com/graphql"),
    (HOME, "https://fr.indeed.com.example.test/"),  # another domain
    (HOME, "https://fr.indeed.co/"),
    (HOME, "https://fr.indeed.fr/"),
    (HOME, "https://www.indeed.fr/"),
    (HOME, "https://fr.notindeed.com/"),
    (HOME, "https://frindeed.com/"),
    (HOME, "https://notindeed.com/"),
    (HOME, "https://fr.indeed.com:8443/"),  # another port
    (HOME, "https://user@fr.indeed.com/"),
    (HOME, "https://fr.indeed.com./"),
    (HOME, "https://www.linkedin.com/"),  # another platform's family
    (HOME, "https://elsewhere.example.test/"),
    (HOME, "javascript:alert(1)"),
    (HOME, ""),
    (HOME, None),
    ("https://www.linkedin.com/", "https://fra.linkedin.com/"),
    ("https://www.linkedin.com/", "https://media.linkedin.com/"),
    ("https://www.linkedin.com/", "https://www.linkedin.com.example.test/"),
    ("https://www.welcometothejungle.com/", "https://fr.welcometothejungle.com/"),  # no countries
    ("https://www.welcometothejungle.com/", "https://cdn.welcometothejungle.com/"),
    ("https://www.welcometothejungle.com/", "https://abc-dsn.algolia.net/"),  # a POST host only
    ("http://www.indeed.test:8080/b19/", "http://fr.indeed.test:8081/b19/"),
    ("http://www.indeed.test:8080/b19/", "https://fr.indeed.test:8080/b19/"),
    ("http://www.indeed.test/b19/", "http://fr.indeed.test/b19/"),  # the stand in's port only
    ("https://jobs.example.test/", "https://www.jobs.example.test/"),  # no family: the origin
    ("https://jobs.example.test/", "https://fr.example.test/"),
]


@pytest.mark.parametrize(("start", "url"), ON)
def test_an_address_on_the_start_s_family_is_on_its_website(start, url):
    assert discovery.on_site(url, start)
    assert finder.on_family(url, start)


@pytest.mark.parametrize(("start", "url"), OFF)
def test_anything_else_is_off_its_website(start, url):
    assert not discovery.on_site(url, start)
    # The api reads every address canonical (finder.canonical drops a final dot); browserd,
    # which loads the page, refuses that host.
    if url is not None and not url.endswith("com./"):
        assert not finder.on_family(url, start)


def test_the_two_copies_of_the_families_are_identical():
    browserd = {(f.domain, f.countries, f.scheme, f.port) for f in discovery.FAMILIES}
    assert browserd == set(finder.FAMILIES)
    assert discovery.COUNTRY_LABEL.pattern == finder.COUNTRY_LABEL.pattern
    assert {f.platform for f in discovery.PLATFORM_FAMILIES} <= set(
        finder_platforms := ("indeed", "linkedin", "welcome_to_the_jungle")
    )
    assert len(finder_platforms) == len(discovery.PLATFORM_FAMILIES)


@pytest.mark.parametrize(
    ("start", "hosts"),
    [
        (HOME, ["indeed.com", "*.indeed.com"]),
        ("https://fr.indeed.com/jobs", ["indeed.com", "*.indeed.com"]),
        ("https://www.linkedin.com/", ["linkedin.com", "*.linkedin.com"]),
        (
            "https://www.welcometothejungle.com/en/jobs",
            ["welcometothejungle.com", "www.welcometothejungle.com"],
        ),
        ("http://www.indeed.test:8080/b19/", ["indeed.test", "*.indeed.test"]),
        ("https://jobs.example.test/search", ["jobs.example.test"]),
        ("https://secure.indeed.com/", ["secure.indeed.com"]),  # no member: its own host
        ("http://pages:8080/indeed/", ["pages"]),  # the journeys' stand in start
    ],
)
def test_the_lease_opens_the_family_and_browserd_narrows_it(start, hosts):
    assert finder.lease_hosts(start) == hosts
    found = tab_of(Page())
    found.request = SimpleNamespace(hosts=tuple(hosts), purpose="sweep")
    found.state.scope = discovery.Scope(start, ("robot",))
    found.state.discovery = True
    assert found.allowed(start)
    if hosts[-1].startswith("*."):  # on the list, off the family: refused in a discovery
        assert not found.allowed(f"https://secure.{hosts[0]}/")
        assert not found.allowed(f"https://fra.{hosts[0]}/")


def scope(start: str = HOME, *, signed_in: bool = False):
    raw = {"start": start, "titles": ["robot"], "places": ["Paris"], "filters": {}}
    return discovery.Scope.parse({**raw, **({"signed_in": True} if signed_in else {})})


@pytest.mark.parametrize("extra", [{"country": True}, {"country": False}, {"platform": "indeed"}])
def test_the_old_country_flag_and_any_other_key_refuse_the_scope(extra):
    raw = {"start": HOME, "titles": ["robot"], "places": [], "filters": {}, **extra}
    assert discovery.Scope.parse(raw) is None


def test_every_other_rule_holds_on_the_family():
    anonymous = scope()
    assert discovery.read_url("https://fr.indeed.com/jobs?q=robot", HOME, scope=anonymous)
    assert discovery.read_url(
        "https://de.indeed.com/jobs?q=robot&location=Paris", HOME, scope=anonymous
    )
    for refused in (
        "https://fr.indeed.com/jobs?q=other",
        "https://fr.indeed.com/jobs?q=robot&l=",  # anonymous: the keys of FIELDS only
        "https://fr.indeed.com/apply",
        "https://fr.indeed.com/account/login",
        "https://fr.indeed.com/jobs/42/candidature",
        "https://secure.indeed.com/jobs?q=robot",
    ):
        assert not discovery.read_url(refused, HOME, scope=anonymous), refused


# --- browserd, anonymous discovery: a redirect within the family is followed ------------------


def tab(start_scope, page_url: str = "about:blank"):
    page = Page(page_url)
    found = tab_of(page)
    found.request = SimpleNamespace(hosts=("indeed.com", "*.indeed.com"), purpose="sweep")
    found.state.scope = start_scope
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


def test_the_home_s_redirect_to_a_country_site_is_followed_and_the_search_goes_on():
    found, sent = tab(scope())
    paused(found, HOME, 302, FR)
    paused(found, FR)  # Chromium's request to the country site, at the Request stage
    paused(found, "https://fr.indeed.com/jobs?q=robot")
    # Later too, and to another country site: the scope never moves, the family holds.
    paused(found, "https://fr.indeed.com/jobs?q=robot", 302, "https://de.indeed.com/jobs?q=robot")
    assert sent == ["Fetch.continueRequest"] * 4
    assert not found.state.blocked
    assert found.state.scope == scope()
    paused(found, "https://fr.indeed.com/apply")
    assert sent[-1] == "Fetch.failRequest"


@pytest.mark.parametrize(
    "location",
    [
        "https://fra.indeed.com/",
        "http://fr.indeed.com/",
        "https://secure.indeed.com/auth",
        "https://fr.indeed.com/apply",
        "https://fr.indeed.com/?r=us",  # anonymous: a key outside FIELDS, as before
        "https://elsewhere.example.test/",
        "/\\fr.indeed.com/",
        "https://fr.indeed.com/\t",
        " " + FR,
    ],
)
def test_an_anonymous_redirect_anywhere_else_is_refused(location):
    found, sent = tab(scope())
    paused(found, HOME, 302, location)
    assert sent == ["Fetch.failRequest"]
    assert found.state.blocked == [HOME]  # the address it was refused at, for the host log


# --- browserd, signed in discovery: a redirect hop within the family --------------------------


def hop(start_scope, url: str):
    context = b17.Context([b17.Page()])
    found = b17.tab_on(context)
    found.request = SimpleNamespace(hosts=("indeed.com", "*.indeed.com"), purpose="sweep")
    asyncio.run(found.begin_discovery(start_scope, "browser_navigate", {"url": start_scope.start}))
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


@pytest.mark.parametrize(
    "url",
    [
        FR,
        "https://fr.indeed.com/?r=us",  # signed in: any hop within the family, its query too
        "https://fr.indeed.com/jobs?q=robot&l=Lyon&from=home",
        "https://de.indeed.com/",
        "https://indeed.com/",
    ],
)
def test_a_signed_in_redirect_within_the_family_is_followed(url):
    found, stopped = hop(scope(signed_in=True), url)
    assert not stopped
    assert not found.state.blocked
    assert not found.state.signin_seen


@pytest.mark.parametrize(
    ("url", "signin"),
    [
        ("https://secure.indeed.com/auth?continue=%2F", True),  # its sign in host: the wall
        ("https://secure.indeed.com/", True),
        ("https://fr.indeed.com/account/login?next=/", True),
        ("https://fra.indeed.com/", False),
        ("http://fr.indeed.com/", False),
        ("https://fr.indeed.com/apply", False),
        ("https://fr.indeed.com/jobs/42/candidature", False),
        ("https://fr.indeed.com/logout", False),
        ("https://employers.indeed.com/", False),
        ("https://elsewhere.example.test/", False),
    ],
)
def test_a_signed_in_redirect_anywhere_else_is_stopped(url, signin):
    found, stopped = hop(scope(signed_in=True), url)
    assert len(stopped) == 1
    assert found.state.blocked == [url]
    assert found.state.signin_seen is signin


def test_the_stopped_target_s_host_is_logged_never_its_path_or_query(caplog):
    found, _ = hop(scope(signed_in=True), "https://secure.indeed.com/auth?token=e2e-8f3a1c")
    found.state.page.url = HOME
    found.on_list = lambda page: True
    with caplog.at_level("WARNING", logger="browserd"):
        answer = asyncio.run(found.settle({"ok": True}))
    assert answer["error"] == "signin_required"
    [line] = [r.getMessage() for r in caplog.records if "allow list" in r.getMessage()]
    assert line.endswith("host=secure.indeed.com")
    assert "auth" not in line
    assert "e2e-8f3a1c" not in line


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://fr.indeed.com/jobs?q=robot", "fr.indeed.com"),
        ("https://User:pw@Secure.Indeed.com:8443/auth", "secure.indeed.com"),
        ("discovery", "unreadable"),
        ("about:blank", "unreadable"),
        ("https://exa mple.test/", "unreadable"),
        ("https://xn--fr-xyz.example/", "xn--fr-xyz.example"),
        ("https://[::1]/", "unreadable"),
        (None, "unreadable"),
    ],
)
def test_the_host_logged_is_the_host_alone(url, host):
    assert b17.actions.host_of(url) == host


# --- the api: the search runs on the family -----------------------------------------------------


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
    assert session.on_site("https://fr.indeed.com/jobs/robot-engineer")
    assert session.on_site("https://de.indeed.com/viewjob?jk=abc")  # the whole family
    assert not session.on_site("https://secure.indeed.com/")
    assert "country" not in site.scopes[0]
    assert leases.acquired[0][-1] == ["indeed.com", "*.indeed.com"]


@pytest.mark.parametrize(
    "landing",
    ["https://fra.indeed.com/", "https://secure.indeed.com/", "https://elsewhere.example.test/"],
)
def test_an_indeed_search_landing_off_its_family_never_starts_the_agent(landing):
    site = Country(landing, results={"robot": ["Robot Engineer"]})
    started: list[Any] = []

    async def agent(session):
        started.append(session)
        return finder.Ran()

    entry, _, _ = run(
        selected(url=HOME, title_include={"terms": ["robot"]}),
        site,
        ScriptedFinder(agent),
        policy=Policy(),
        sweep_id="00000000-0000-4000-8000-000000000718",
    )
    assert entry["error"] in {"page_failed", "unsupported_interaction"}
    assert not started


def test_a_website_on_no_family_keeps_its_one_origin():
    start = "https://www.example-jobs.test/"
    site = SearchSite(base=start, results={"robot": ["Robot Engineer"]})
    entry, _, leases = run(selected(url=start), site, ScriptedFinder(searching(site)))
    assert not finder.on_family("https://fr.example-jobs.test/", start)
    assert entry["searches"] == 1
    assert leases.acquired[0][-1] == ["www.example-jobs.test"]


def test_a_session_refuses_a_landing_off_the_family():
    plain = finder.InteractiveScope(HOME, ("robot",))
    session = finder.FinderSession(None, HOME, "", "e2e-b19", interactive=plain, landed=FR)
    assert session.on_site("https://uk.indeed.com/jobs")
    with pytest.raises(ValueError, match="another website"):
        finder.FinderSession(
            None, HOME, "", "e2e-b19", interactive=plain, landed="https://fra.indeed.com/"
        )
    assert "country" not in plain.tool_scope()
