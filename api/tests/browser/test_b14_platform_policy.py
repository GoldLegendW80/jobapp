"""Browser B14 without a database: the platform of an address, the wall pages, the codes, and a
careers page saved at a protected platform's address (app/browser/platform_policy.py)."""

import asyncio
import itertools

import pytest

from app.browser import finder, platform_policy, sources
from app.browser.platform_policy import CUSTOM, platform_of, wall
from app.browser.sources import ERRORS, PAGE_PAUSE_S, linkedin
from app.browser.sources.interactive import SourceContext
from app.jobs import sweep
from tests.browser.test_selected_interactive import execute, selected

PLATFORM_HOSTS = [
    ("https://www.linkedin.com/jobs/", "linkedin"),
    ("https://linkedin.com/jobs", "linkedin"),
    ("https://fr.linkedin.com/jobs", "linkedin"),
    ("https://WWW.LINKEDIN.COM/jobs", "linkedin"),
    ("https://www.linkedin.com./jobs", "linkedin"),
    ("https://www.linkedin.com:443/jobs", "linkedin"),
    ("https://e2e@www.linkedin.com/jobs", "linkedin"),
    ("https://linkedin.com.example.test/jobs", "linkedin"),  # fail closed: names the platform
    ("https://lnkd.in/abc", "linkedin"),
    ("https://www.indeed.com/", "indeed"),
    ("https://fr.indeed.com/emplois?q=robot", "indeed"),
    ("https://www.indeed.fr/emplois", "indeed"),
    ("https://uk.indeed.co.uk/jobs", "indeed"),
    ("https://secure.indeed.com/auth", "indeed"),
    ("https://ｉｎｄｅｅｄ.com/", "indeed"),  # full width letters fold to the same host
    ("https://www.free-work.com/fr/tech-it/jobs", "freework"),
    ("https://free-work.fr/", "freework"),
    ("https://freework.com/", "freework"),
    ("https://www.welcometothejungle.com/fr/jobs", "welcome_to_the_jungle"),
    ("https://welcometothejungle.co/", "welcome_to_the_jungle"),
    ("https://app.wttj.co/jobs", "welcome_to_the_jungle"),
]
OTHER_HOSTS = [
    "https://jobs.example-robotics.test/j15/",
    "https://careers.example.org/jobs",
    "https://notindeed.com/",
    "https://indeedjobs.example.org/",
    "https://indeed-careers.example.org/",
    "https://linkedinjobs.example.org/",
    "https://example.org/linkedin.com/jobs",  # the platform in the path, not the host
    "https://example.org/?next=https://www.indeed.com/",
    "https://freeworks.example.org/",
    "https://jungle.example.org/",
    "https://welcome.example.org/to/the/jungle",
    "http://pages:8080/b8/indeed/",
    "https://192.0.2.10/jobs",
    "not an address",
    "",
]


@pytest.mark.parametrize(("url", "platform"), PLATFORM_HOSTS)
def test_a_platform_address_is_that_platform_whatever_its_form(url, platform):
    assert platform_of(url) == platform


@pytest.mark.parametrize("url", OTHER_HOSTS)
def test_another_address_is_a_website_of_its_own(url):
    assert platform_of(url) == CUSTOM


def test_a_custom_label_does_not_hide_the_platform_of_the_address():
    context = SourceContext("e2e-person", "e2e-sweep", "id", "custom", "https://www.indeed.fr/")
    assert platform_policy.platform_for(context) == "indeed"
    named = SourceContext("e2e-person", "e2e-sweep", "id", "indeed", "http://pages:8080/b8/indeed/")
    assert platform_policy.platform_for(named) == "indeed"  # the test fixture keeps its platform


RESULTS = '- heading "Robot Engineer jobs in Paris" [level=1]\n- link "Robot Engineer" [ref=e3]'
WALLS = [
    ("indeed", "https://fr.indeed.com/checkpoint/", RESULTS, "permission_checkpoint"),
    ("indeed", "https://fr.indeed.com/captcha?x=1", RESULTS, "permission_checkpoint"),
    (
        "indeed",
        "https://fr.indeed.com/cdn-cgi/challenge-platform/h/b",
        RESULTS,
        "permission_checkpoint",
    ),
    ("indeed", "https://fr.indeed.com/a/challenge/", RESULTS, "permission_checkpoint"),
    ("indeed", "https://fr.indeed.com/blocked", RESULTS, "permission_checkpoint"),
    ("indeed", "https://secure.indeed.com/auth", RESULTS, "platform_signin"),
    ("indeed", "https://fr.indeed.com/account/login", RESULTS, "platform_signin"),
    ("freework", "https://www.free-work.com/fr/login", RESULTS, "platform_signin"),
    ("freework", "https://www.free-work.com/fr/connexion", RESULTS, "platform_signin"),
    (
        "welcome_to_the_jungle",
        "https://www.welcometothejungle.com/fr/signin",
        RESULTS,
        "platform_signin",
    ),
    (
        "welcome_to_the_jungle",
        "https://www.welcometothejungle.com/fr/Sign-In",
        RESULTS,
        "platform_signin",
    ),
    (
        "indeed",
        "https://fr.indeed.com/",
        '- heading "Verify you are human" [level=1]',
        "permission_checkpoint",
    ),
    (
        "indeed",
        "https://fr.indeed.com/",
        '- heading "Are you a robot?" [level=2]',
        "permission_checkpoint",
    ),
    (
        "indeed",
        "https://fr.indeed.com/",
        '- heading "Just a moment..." [level=1]',
        "permission_checkpoint",
    ),
    (
        "indeed",
        "https://fr.indeed.com/",
        '- heading "Access denied" [level=1]',
        "permission_checkpoint",
    ),
    (
        "freework",
        "https://www.free-work.com/",
        '- heading "Vérifiez que vous êtes humain" [level=1]',
        "permission_checkpoint",
    ),
    (
        "freework",
        "https://www.free-work.com/",
        '- heading "ÊTES-VOUS UN ROBOT ?" [level=1]',
        "permission_checkpoint",
    ),
    (
        "freework",
        "https://www.free-work.com/",
        '- heading "Accès refusé" [level=1]',
        "permission_checkpoint",
    ),
    (
        "indeed",
        "https://fr.indeed.com/",
        '- heading "Unusual activity" [level=1]',
        "permission_checkpoint",
    ),
    ("indeed", "https://fr.indeed.com/", '- heading "Sign in" [level=1]', "platform_signin"),
    ("indeed", "https://fr.indeed.com/", '- heading "Se connecter" [level=1]', "platform_signin"),
    (
        "welcome_to_the_jungle",
        "https://www.welcometothejungle.com/",
        '- heading "Connexion" [level=1]',
        "platform_signin",
    ),
    ("linkedin", "https://www.linkedin.com/checkpoint/challenge/", RESULTS, "linkedin_checkpoint"),
    ("linkedin", "https://www.linkedin.com/login", RESULTS, "linkedin_signin"),
    ("linkedin", "https://www.linkedin.com/authwall?x=1", RESULTS, "linkedin_signin"),
    (
        "linkedin",
        "https://www.linkedin.com/jobs/",
        '- heading "Let\'s do a quick security check" [level=1]',
        "linkedin_checkpoint",
    ),
    (
        "linkedin",
        "https://www.linkedin.com/jobs/",
        '- heading "Verify you are human" [level=1]',
        "linkedin_checkpoint",
    ),
    (
        "linkedin",
        "https://www.linkedin.com/jobs/",
        '- heading "Sign in" [level=1]',
        "linkedin_signin",
    ),
]
NOT_WALLS = [
    ("indeed", "https://fr.indeed.com/emplois?q=robot", RESULTS),
    ("indeed", "https://fr.indeed.com/viewjob?jk=123", '- heading "Security engineer" [level=1]'),
    ("indeed", "https://fr.indeed.com/emplois", '- link "Sign in" [ref=e2]'),  # a link, not a page
    ("indeed", "https://fr.indeed.com/emplois", "- paragraph: captcha is not a heading"),
    (
        "indeed",
        "https://fr.indeed.com/emplois",
        '- heading "Sign in to see more jobs today" [level=3]',
    ),
    ("indeed", "https://fr.indeed.com/jobs/login-engineer-123", RESULTS),  # a slug, not a segment
    ("indeed", "https://fr.indeed.com/emplois", '- heading "Robot verifying engineer" [level=1]'),
    (
        "freework",
        "https://www.free-work.com/fr/tech-it/jobs",
        '- heading "Connexions réseau" [level=1]',
    ),
    (
        "freework",
        "https://www.free-work.com/fr/tech-it/jobs",
        '- heading "Login systems engineer" [level=2]',
    ),
    ("welcome_to_the_jungle", "https://www.welcometothejungle.com/fr/companies/x", RESULTS),
    (
        "welcome_to_the_jungle",
        "https://www.welcometothejungle.com/fr/jobs",
        '- heading "Authentification forte, Paris" [level=2]',
    ),
    ("linkedin", "https://www.linkedin.com/jobs/search/?keywords=robot", RESULTS),
    (
        "linkedin",
        "https://www.linkedin.com/jobs/view/1234567/",
        '- heading "Robot Engineer" [level=1]',
    ),
    ("indeed", "https://fr.indeed.com/emplois", ""),
]


@pytest.mark.parametrize(("platform", "url", "snapshot", "code"), WALLS)
def test_a_wall_page_is_told_by_its_address_or_its_heading(platform, url, snapshot, code):
    assert wall(platform, url, snapshot) == code


@pytest.mark.parametrize(("platform", "url", "snapshot"), NOT_WALLS)
def test_an_ordinary_page_is_not_a_wall(platform, url, snapshot):
    assert wall(platform, url, snapshot) is None


def test_a_huge_snapshot_is_read_in_linear_time():
    text = '- heading "Robot Engineer" [level=2]\n' * 4000 + '- heading "Access denied" [level=1]'
    assert wall("indeed", "https://fr.indeed.com/", text) == "permission_checkpoint"


@pytest.mark.parametrize(
    ("platform", "refused", "reserved", "code"),
    [
        ("indeed", "indeed_off", False, "permission_unavailable"),
        ("indeed", "indeed_off", True, "permission_revoked"),
        ("freework", "freework_paused", True, "permission_checkpoint"),
        ("linkedin", "linkedin_off", False, "permission_unavailable"),
        ("linkedin", "linkedin_paused", False, "permission_checkpoint"),
        ("indeed", "platform_off", False, "permission_unavailable"),
        ("indeed", "something_new", False, "permission_unavailable"),
    ],
)
def test_every_refusal_becomes_a_source_code(platform, refused, reserved, code):
    assert platform_policy.source_code(platform, refused, reserved) == code
    assert code in ERRORS


class Allow:
    """A policy that grants every check: the pace alone is under test."""

    async def check(self, context):
        return None

    async def reserve(self, context):
        return None

    async def observe(self, context, url, snapshot):
        return None


class Paged:
    """Browser tools that answer every address with a page linking to its next page, and note
    the time of each browser call (the runner's fake clock)."""

    def __init__(self, now):
        self.now, self.current, self.calls = now, "", []

    async def call(self, name, args):
        self.calls.append((self.now[0], name, args.get("url", "")))
        if name == "browser_navigate":
            self.current = args["url"]
        return {"ok": True, "url": self.current}

    async def snapshot(self):
        page = '- heading "Jobs" [level=1]\n- link "Next" [ref=e1]\n  - /url: ?page=2'
        return {"ok": True, "url": self.current, "snapshot": page}

    async def wait_for(self, **_args):
        return {"ok": True}


async def two_pages(session):
    await session.navigate("?page=2")
    return finder.Ran()


@pytest.mark.parametrize(
    "second", ["https://fr.linkedin.com/jobs/", "https://linkedin.com/jobs/search/"]
)
def test_two_linkedin_addresses_of_one_sweep_share_linkedin_s_pace(second):
    """The preset LinkedIn address and a custom one of another spelling are paced as one
    platform: no page of the sweep follows another LinkedIn page sooner than LinkedIn's
    shortest pause, across the two sources too."""
    from tests.browser.test_b9_site import ScriptedFinder

    now = [0.0]  # the runner's clock: its sleep, its pacer and the tools read the same time
    tools = Paged(now)
    sites = [("linkedin", "https://www.linkedin.com/jobs/"), ("custom", second)]
    search = selected(
        interactive_sites=[
            {"platform": label, "url": url, "enabled": True, "mode": "llm_browser"}
            for label, url in sites
        ]
    )
    entries, _, leases, _, _ = asyncio.run(
        execute(
            search,
            "e2e-person",
            ScriptedFinder(two_pages),
            tools=tools,
            policy=Allow(),
            sweep_id="b14-sweep",
            now=now,
            deadline=10_000,
        )
    )
    assert len(leases.acquired) == 2, entries
    opened = [at for at, name, _ in tools.calls if name == "browser_navigate"]
    assert len(opened) == 4, tools.calls  # two pages per source
    low = linkedin.PAUSE_S[0]
    gaps = [b - a for a, b in itertools.pairwise(opened)]
    assert all(gap >= low for gap in gaps), gaps
    assert platform_policy.pace_key("linkedin", "fr.linkedin.com") == (
        platform_policy.pace_key("linkedin", "www.linkedin.com")
    )


def test_linkedin_keeps_its_slower_pace_and_has_no_page_cap():
    """Owner, 2026-10-28: no page cap and no daily limit, LinkedIn too; its pace stays."""
    assert platform_policy.pause_for("linkedin") == linkedin.PAUSE_S == (10.0, 30.0)
    assert platform_policy.pause_for("indeed") == PAGE_PAUSE_S
    assert not hasattr(platform_policy, "PAGES")
    assert not hasattr(linkedin, "RESULT_PAGES")


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "freework", "welcome_to_the_jungle"])
def test_tests_open_the_fixture_server_never_the_platform(platform):
    url = platform_policy.test_start("http://pages:8080/b8", platform, "https://www.indeed.com/")
    assert url == f"http://pages:8080/b8/{platform}/"
    assert platform_policy.test_start(None, platform, "https://www.indeed.com/") == (
        "https://www.indeed.com/"
    )
    assert platform_policy.test_start("http://pages:8080/b8", "custom", "https://x.example/") == (
        "https://x.example/"
    )


def test_a_policy_without_a_sweep_refuses_every_platform_and_lets_custom_sites_through():
    policy = platform_policy.PlatformPolicy(lambda: pytest.fail("no database read"))  # type: ignore[arg-type,return-value]
    protected = SourceContext("e2e-person", None, "id", "indeed", "https://fr.indeed.com/")
    custom = SourceContext("e2e-person", None, "id", "custom", "https://careers.example.org/")
    assert asyncio.run(policy.check(protected)) == "permission_unavailable"
    assert asyncio.run(policy.reserve(protected)) == "permission_unavailable"
    assert asyncio.run(policy.check(custom)) is None
    assert asyncio.run(policy.reserve(custom)) is None
    assert asyncio.run(policy.observe(custom, "https://careers.example.org/login", "")) is None


@pytest.mark.parametrize("selection", ["indeed", "none", "custom"])
def test_a_careers_page_at_a_protected_address_never_runs_the_generic_finder(
    selection, monkeypatch
):
    address = (
        "https://www.indeed.fr/emplois" if selection != "custom" else "https://careers.example.org/"
    )
    legacy = {"kind": "site", "query": address, "enabled": True}
    search = selected(
        "https://fr.indeed.com/" if selection == "indeed" else "https://careers.example.org/jobs",
        browser_sources=[legacy],
    )
    if selection == "none":
        search.interactive_sites = []
    calls = []

    async def legacy_one(self, plan, stats, source, entry, store, deadline, progress):
        calls.append(source.kind)
        return None, None

    async def interactive(*_args):
        return None

    monkeypatch.setattr(sources.Runner, "_one", legacy_one)
    monkeypatch.setattr("app.browser.sources.interactive.run_selected", interactive)
    entries, stored, leases, _, _ = asyncio.run(execute(search, "e2e-person", None))
    entry = next(e for e in entries if e["mode"] == "legacy_finder")
    assert entry["source_id"] == sweep._source_id("legacy_browser", "site", address, "")
    if selection == "indeed":
        assert calls == []
        assert entry["reason"] == "interactive_selected"
    elif selection == "none":
        assert calls == []
        assert entry["error"] == "permission_unavailable"
        assert entry["outcome"] == "failed"
    else:
        assert calls == ["site"]
    assert not stored
    assert not leases.acquired
