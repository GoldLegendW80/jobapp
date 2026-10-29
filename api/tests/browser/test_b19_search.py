# ruff: noqa: E501  tables of addresses, one per line
"""B19: a signed in search of a real job website through its own search box (owner,
2026-10-29: "Let it use the search boxes"). Signed in, the agent may type one saved title or
place into a text box of the page, in a GET search form or in no form (discovery.search_box,
Scope.types), press Enter there, click a button or link whose every name is a search word
(discovery.search_words), and open a posting or a result list of the platform
(discovery.posting, listing, page_url); a page's fetch or XHR may POST to the platform's family
or its search service (discovery.posts). The submit, apply and danger detectors run first and
win; anonymous reading keeps the native GET rules. Each rule is a table of what it accepts and
what it refuses, near misses included (implementer.md, Heuristic code).
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from app.browser.sources import interactive
from tests.browser import test_b17_browserd as b17
from tests.browser.test_b5_actions import facts
from tests.browser.test_j18_scope import discovery

INDEED = "https://www.indeed.com/"
LINKEDIN = "https://www.linkedin.com/"
JUNGLE = "https://www.welcometothejungle.com/en/jobs"
TITLES = ["GenAI Engineer", "Ingénieur robotique"]
PLACES = ["Paris", "Saint-Étienne"]


def scope(start: str = LINKEDIN, *, signed_in: bool = True) -> discovery.Scope:
    raw = {"start": start, "titles": TITLES, "places": PLACES, "filters": {}}
    return discovery.Scope.parse({**raw, **({"signed_in": True} if signed_in else {})})


def control(
    *names: str,
    tag: str = "input",
    kind: str = "text",
    role: str = "",
    in_form: bool = False,
    method: str = "",
    action: str = "",
    href: str = "",
    **more: Any,
) -> dict[str, Any]:
    """What FACTS_JS answers on a signed in page (scripts on: `native` false), its click point
    the element itself."""
    shape = facts(*names, tag=tag, kind=kind, in_form=in_form, **more)
    shape["role"] = role
    shape["discovery"] = {
        "native": False,
        "method": method,
        "action": action,
        "target": "",
        "name": "",
        "href": href,
        "fields": [],
    }
    shape["point"] = {**shape, "inside": True}
    return shape


def judge(shape, tool: str, *, text: object = None, key: bool = False, start=LINKEDIN, **kw):
    args = {} if text is None else {"text": text}
    return discovery.verdict(
        shape,
        tool,
        start,
        scope=scope(start, **kw),
        args=args,
        action="key" if key else None,
    )


# --- the text box ------------------------------------------------------------------------------

BOXES = [
    control("Search by title, skill, or company"),
    control("Search", kind="search"),
    control("City", role="combobox"),
    control("Keywords", kind="search", role="searchbox"),
    control("What", tag="textarea", kind="textarea", rows=1),
    control("Search", tag="div", kind="", role="combobox", editable=True),
    control("Search", tag="div", kind="", role="searchbox", editable=True),
]
NOT_BOXES = [
    control("Email", kind="email"),
    control("Password", kind="password"),
    control("Phone", kind="tel"),
    control("Website", kind="url"),
    control("Salary", kind="number"),
    control("q", kind="hidden"),
    control("Remote", kind="checkbox"),
    control("Search", kind="submit"),
    control("Search", kind="button"),
    control("Why us?", tag="textarea", kind="textarea", rows=2),
    control("Why us?", tag="textarea", kind="textarea", rows=0),
    control("Notes", tag="div", kind="", role="textbox", editable=True),
    control("Search", tag="div", kind="", role="combobox", editable=False),
    control("Search", tag="span", kind="", role="searchbox"),
    control("Country", tag="select", kind="select-one"),
    control("Search", tag="button", kind="submit"),
    control("Search", tag="a", kind=""),
]


@pytest.mark.parametrize("shape", BOXES)
def test_a_text_box_of_the_page_is_a_search_box(shape):
    assert discovery.search_box(shape)


@pytest.mark.parametrize("shape", NOT_BOXES)
def test_anything_else_is_no_search_box(shape):
    assert not discovery.search_box(shape)


@pytest.mark.parametrize(
    ("names", "kind"),
    [
        (["City, state, or zip code", "jobs-search-box-location-id-ember26"], "place"),
        (["Search by title, skill, or company", "jobs-search-box-keyword-id-ember25"], "title"),
        (["Où ?"], "place"),
        (["Ville, département"], "place"),
        (["Quoi ?"], "title"),
        (["Métier, mot-clé"], "title"),
        (["Job title, keywords, or company"], "title"),
        (["Standort"], "place"),
        (["Stellen"], "title"),
        (["WHERE"], "place"),
        (["Location", "Keywords"], None),  # two names that disagree: cannot tell
        (["Job location"], None),  # one name holding both
        (["ou"], None),  # the French "or", no place
        (["ember25"], None),
        ([], None),
        ("Where", None),  # not a list
    ],
)
def test_the_box_kind_is_told_from_its_names(names, kind):
    assert discovery.box_kind(names) == kind


@pytest.mark.parametrize(
    ("names", "text", "takes"),
    [
        (["Keywords"], "GenAI Engineer", True),
        (["Keywords"], "genai engineer", True),  # case aside
        (["Keywords"], "Ingenieur robotique", True),  # accents aside
        (["Keywords"], "Paris", False),  # a place in a title box
        (["Where"], "Paris", True),
        (["Where"], "saint-etienne", True),
        (["Where"], "GenAI Engineer", False),  # a title in a place box
        (["ember25"], "Paris", True),  # cannot tell: either
        (["ember25"], "GenAI Engineer", True),
        (["Keywords"], "", True),  # nothing, to clear it
        (["Keywords"], "GenAI", False),
        (["Keywords"], "GenAI Engineer ", False),
        (["Keywords"], "Ingénieur IA", False),  # another title, a translation
        (["Keywords"], "e2e-candidate@example.org", False),
        (["Keywords"], "GenAI​Engineer", False),
        (["Keywords"], "ＧｅｎＡＩ Engineer", False),  # a full width form
        (["Keywords"], None, False),
        (["Keywords"], 42, False),
        (["Keywords"], "x" * 301, False),
    ],
)
def test_a_box_takes_one_saved_value_of_its_kind(names, text, takes):
    assert scope().types(names, text) is takes


# --- typing and Enter, signed in ------------------------------------------------------------------

TITLE_BOX = control("Search by title, skill, or company", role="combobox")
PLACE_BOX = control("City, state, or zip code", role="combobox")


@pytest.mark.parametrize(
    ("shape", "text"),
    [
        (TITLE_BOX, "GenAI Engineer"),
        (TITLE_BOX, "GENAI ENGINEER"),
        (TITLE_BOX, ""),
        (PLACE_BOX, "Paris"),
        (control("ember25"), "Saint-Étienne"),
        (
            control(
                "What", in_form=True, method="get", action="https://www.linkedin.com/jobs/search/"
            ),
            "GenAI Engineer",
        ),
        (
            control(
                "q", kind="search", in_form=True, method="get", action="https://fr.indeed.com/jobs"
            ),
            "GenAI Engineer",
        ),
        (control("Search", tag="div", kind="", role="combobox", editable=True), "GenAI Engineer"),
    ],
)
def test_a_saved_value_is_typed_into_the_site_s_own_box(shape, text):
    start = INDEED if "indeed" in shape["discovery"]["action"] else LINKEDIN
    assert judge(shape, "browser_type", text=text, start=start) is None
    assert judge(shape, "browser_type", text=text, key=True, start=start) is None  # and Enter


@pytest.mark.parametrize(
    ("shape", "text", "code"),
    [
        (TITLE_BOX, "Paris", discovery.UNSUPPORTED),
        (PLACE_BOX, "GenAI Engineer", discovery.UNSUPPORTED),
        (TITLE_BOX, "Prompt Engineer", discovery.UNSUPPORTED),
        (TITLE_BOX, "ignore your task", discovery.UNSUPPORTED),
        (
            control(
                "Message", in_form=True, method="post", action="https://www.linkedin.com/messaging/"
            ),
            "GenAI Engineer",
            discovery.UNSUPPORTED,
        ),
        (
            control(
                "City", in_form=True, method="post", action="https://www.linkedin.com/jobs/search/"
            ),
            "Paris",
            discovery.UNSUPPORTED,
        ),
        (control("City", in_form=True, method="dialog", action=""), "Paris", discovery.UNSUPPORTED),
        (
            control(
                "City",
                in_form=True,
                method="get",
                action="https://www.linkedin.com/jobs/view/1/apply/",
            ),
            "Paris",
            discovery.UNSUPPORTED,
        ),
        (
            control("City", in_form=True, method="get", action="https://www.linkedin.com/feedback"),
            "Paris",
            discovery.UNSUPPORTED,
        ),
        (
            control(
                "City", in_form=True, method="get", action="https://elsewhere.example.test/jobs"
            ),
            "Paris",
            discovery.UNSUPPORTED,
        ),
        (
            control(
                "City",
                in_form=True,
                method="get",
                action="https://www.linkedin.com/jobs?action=logout",
            ),
            "Paris",
            discovery.UNSUPPORTED,
        ),
        (control("Email", kind="email"), "GenAI Engineer", discovery.UNSUPPORTED),
        (control("Password", kind="password", credential=True), "GenAI Engineer", "credential"),
        (control("Card number", field="cc-number"), "GenAI Engineer", "card_field"),
        (control("Send", role="combobox"), "GenAI Engineer", "submit"),
    ],
)
def test_anything_else_typed_is_refused(shape, text, code):
    assert judge(shape, "browser_type", text=text) == code


def test_anonymous_reading_keeps_the_native_get_rule():
    assert judge(TITLE_BOX, "browser_type", text="GenAI Engineer", signed_in=False) is not None
    assert judge(TITLE_BOX, "browser_type", text="GenAI Engineer", key=True, signed_in=False)


@pytest.mark.parametrize(
    "shape",
    [
        control(
            "City", in_form=True, method="post", action="https://www.linkedin.com/jobs/search/"
        ),
        control("City", in_form=True, form_fields=["cc-number card number"]),
        control("Search", tag="button", kind="submit"),
        control("Notes", tag="textarea", kind="textarea", rows=3),
    ],
)
def test_enter_is_refused_anywhere_else(shape):
    assert judge(shape, "browser_type", text="Paris", key=True) is not None


# --- the search button ---------------------------------------------------------------------------

SEARCH_NAMES = [
    ["Search"], ["SEARCH"], ["search"], ["Search jobs"], ["Search for jobs"], ["Find jobs"],
    ["Find a job"], ["Find"], ["Rechercher"], ["RECHERCHER"], ["Rechércher"], ["Recherche"],
    ["Lancer la recherche"], ["Rechercher des offres"], ["Trouver"], ["Trouver un emploi"],
    ["Suchen"], ["SUCHEN"], ["Jobs suchen"], ["Stellen finden"], ["Suche starten"],
    ["Search 9756 jobs"], ["Search", "search-btn"], ["Search", "searchButton"],
    ["Search", "magnifying glass icon"], ["Rechercher", "loupe"], ["Ｓｅａｒｃｈ"],
    ["  Search  "], ["Search!"], ["search_button", "Search"], ["Search", "go"],
    ["Search", "global-nav-search"], ["Rechercher", "hp-search-button-2"],
]  # fmt: skip
NOT_SEARCH_NAMES = [
    [], ["Save search"], ["Search history"], ["Clear search"], ["Advanced search"],
    ["Recherche avancée"], ["Easy Apply"], ["Apply"], ["Postuler"], ["Candidater"], ["Follow"],
    ["Connect"], ["Message"], ["Se connecter"], ["Sign in"], ["Search and apply"],
    ["Rechercher et postuler"], ["Research"], ["Searching"], ["Search", "Follow"],
    ["Sеarch"], ["Find", "Save"], ["Trouver un mentor"], ["Jobs"], ["icon"],
    ["Search", "follow-btn"], ["Search", "btnShare"], ["Search", "Bewerben"], ["Find", "easyapply"],
    ["Search", "saveSearch"], ["Rechercher", "suivre"], ["Search", "jobAlertToggle"], ["Go"], ["OK"], ["Submit"], ["Envoyer"],
    ["Créer une alerte"], ["Search"] * 61, ["Search Search"], ["Find people"], [""],
]  # fmt: skip


@pytest.mark.parametrize("names", SEARCH_NAMES)
def test_a_name_that_says_search_is_a_search_word(names):
    assert discovery.search_words({"names": names})


@pytest.mark.parametrize("names", NOT_SEARCH_NAMES)
def test_anything_else_is_no_search_word(names):
    assert not discovery.search_words({"names": names})


@pytest.mark.parametrize(
    "shape",
    [
        control("Search", tag="button", kind="submit"),  # outside a form: no form to submit
        control("Search", tag="button", kind="button", in_form=True, method="post"),
        control("Rechercher", tag="input", kind="button"),
        control(
            "Find jobs",
            tag="button",
            kind="submit",
            in_form=True,
            method="get",
            action="https://fr.indeed.com/jobs",
        ),
        control("Search", tag="div", kind="", role="button"),
        control("Search", tag="a", kind="", href="javascript:void(0)"),
        control("Search", tag="a", kind="", href="https://www.linkedin.com/jobs/#search"),
    ],
)
def test_a_search_button_is_pressed(shape):
    start = INDEED if "indeed" in shape["discovery"]["action"] else LINKEDIN
    current = "https://www.linkedin.com/jobs/"
    assert discovery.verdict(shape, "browser_click", current, scope=scope(start), args={}) is None


@pytest.mark.parametrize(
    ("shape", "code"),
    [
        (control("Apply", tag="button", kind="button"), "submit"),
        (control("Postuler", tag="button", kind="button"), "submit"),
        (
            control("Easy Apply", tag="a", kind="", href="https://www.linkedin.com/jobs/view/42/"),
            "submit",
        ),
        (control("Search", "Easy Apply", tag="button", kind="button"), "submit"),
        (control("Delete", tag="button", kind="button"), "danger"),
        (control("Follow", tag="button", kind="button"), discovery.UNSUPPORTED),
        (control("Save search", tag="button", kind="button"), "submit"),
        (
            control(
                "Search",
                tag="button",
                kind="submit",
                in_form=True,
                method="post",
                action="https://www.linkedin.com/jobs/search/",
            ),
            discovery.UNSUPPORTED,
        ),
        (
            control(
                "Search",
                tag="button",
                kind="submit",
                in_form=True,
                method="get",
                action="https://www.linkedin.com/jobs/view/1/apply",
            ),
            discovery.UNSUPPORTED,
        ),
        (
            control("Search", tag="div", kind="", role="button", holds_submit=True),
            discovery.UNSUPPORTED,
        ),
        (
            control("Search", tag="a", kind="", href="https://elsewhere.example.test/"),
            discovery.UNSUPPORTED,
        ),
        (control("Search", tag="input", kind="text"), discovery.UNSUPPORTED),
        (control("Search", tag="button", kind="button", selector_hit=True), "submit"),
    ],
)
def test_anything_else_clicked_is_refused(shape, code):
    assert discovery.verdict(shape, "browser_click", LINKEDIN, scope=scope(), args={}) == code


def test_a_click_that_lands_on_another_element_is_judged_there():
    shape = control("Search", tag="button", kind="button")
    shape["point"] = {**control("Easy Apply", tag="button", kind="button"), "inside": False}
    assert discovery.verdict(shape, "browser_click", LINKEDIN, scope=scope(), args={}) == "submit"
    shape["point"] = {**control("Follow", tag="button", kind="button"), "inside": False}
    assert discovery.verdict(shape, "browser_click", LINKEDIN, scope=scope(), args={})


def test_an_anonymous_search_button_outside_a_native_form_stays_refused():
    shape = control("Search", tag="button", kind="button")
    assert discovery.verdict(shape, "browser_click", LINKEDIN, scope=scope(signed_in=False))


# --- postings and result lists ----------------------------------------------------------------------

PAGES = [
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/?trackingId=abc%3D%3D&refId=x&trk=public_jobs"),
    (LINKEDIN, "https://fr.linkedin.com/jobs/view/genai-engineer-at-example-robotics-4012345678"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/account-executive-at-example-4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&geoId=105015875&currentJobId=4012345678&origin=JOBS_HOME_SEARCH_BUTTON"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?currentJobId=4012345678"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer"),
    (LINKEDIN, "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678"),
    (LINKEDIN, "https://www.linkedin.com/jobs/"),
    (INDEED, "https://fr.indeed.com/viewjob?jk=3f2a9c1d0b8e7a65&from=serp&vjs=3"),
    (INDEED, "https://fr.indeed.com/rc/clk?jk=3f2a9c1d0b8e7a65&bb=Xy&xkcb=SoA&fccid=abc&vjs=3"),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&l=Lyon&from=searchOnHP&vjk=3f2a"),
    (INDEED, "https://fr.indeed.com/jobs?q=&l=Paris"),
    (INDEED, "https://fr.indeed.com/jobs?q=ingenieur+robotique&start=10"),
    (INDEED, "https://www.indeed.com/"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/example-robotics/jobs/genai-engineer_paris_EXAMP_aBc123"),
    (JUNGLE, "https://www.welcometothejungle.com/en/companies/example-robotics/jobs/genai-engineer?q=0f3&o=12"),
    (JUNGLE, "https://www.welcometothejungle.com/en-GB/companies/example/jobs/account-manager"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/jobs?query=GenAI%20Engineer&page=2&aroundQuery=Paris&refinementList%5Boffices.country_code%5D%5B%5D=FR"),
    (JUNGLE, "https://welcometothejungle.com/en/jobs"),
]  # fmt: skip
NOT_PAGES = [
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/apply/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/?openSDUIApplyFlow=true"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678#apply"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/abc/?trk=x"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/4012345678/extra/?trk=x"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/Genai-Engineer-4012345678/?trk=x"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=Prompt%20Engineer"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=Paris"),
    (LINKEDIN, "https://www.linkedin.com/jobs/collections/a/b/?x=1"),
    (LINKEDIN, "https://www.linkedin.com/messaging/thread/42/"),
    (LINKEDIN, "https://www.linkedin.com/in/someone/?miniProfileUrn=x"),
    (LINKEDIN, "https://www.linkedin.com/feed/"),  # the person's own list (round 1, test_b19_guards)
    (LINKEDIN, "https://www.linkedin.com/m/logout/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&action=logout"),
    (LINKEDIN, "https://www.linkedin.com/my-items/saved-jobs/?x=1"),
    (LINKEDIN, "https://elsewhere.example.test/jobs/view/4012345678/"),
    (LINKEDIN, "https://www.linkedin.com/jobs/view/40%2512345678/"),
    (LINKEDIN, "https://user@www.linkedin.com/jobs/view/4012345678/"),
    (INDEED, "https://fr.indeed.com/viewjob?jk=3f2a&jk=other"),
    (INDEED, "https://fr.indeed.com/viewjob?from=serp"),
    (INDEED, "https://fr.indeed.com/viewjob?jk=3f-2a"),
    (INDEED, "https://fr.indeed.com/applystart?jk=3f2a9c1d0b8e7a65"),
    (INDEED, "https://fr.indeed.com/viewjob?jk=3f2a&apply=1"),
    (INDEED, "https://secure.indeed.com/viewjob?jk=3f2a"),
    (INDEED, "https://fr.indeed.com/jobs?q=Prompt+Engineer"),
    (INDEED, "https://fr.indeed.com/account/view?x=1"),
    (INDEED, "https://fr.indeed.com/rc/clk/apply?jk=3f2a"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/example/jobs/genai-engineer/apply"),
    (JUNGLE, "https://www.welcometothejungle.com/xx/companies/example/jobs/genai-engineer?o=1"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/example/jobs"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/exa%20mple/jobs/genai"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/me/settings?x=1"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/jobs?query=Prompt%20Engineer"),
    (JUNGLE, "https://fr.welcometothejungle.com/fr/jobs?query=x"),
    (JUNGLE, "https://www.welcometothejungle.com/fr/companies/example/jobs/payments-engineer"),
]  # fmt: skip


@pytest.mark.parametrize(("start", "url"), PAGES)
def test_a_posting_or_a_result_list_of_the_platform_opens(start, url):
    assert discovery.signed_request(url, "GET", True, scope(start))


@pytest.mark.parametrize(("start", "url"), NOT_PAGES)
def test_anything_else_never_opens(start, url):
    assert not discovery.signed_request(url, "GET", True, scope(start))


def test_a_website_on_no_family_keeps_the_frozen_scope_rule():
    custom = scope("https://jobs.example.test/search")
    assert discovery.signed_request(
        "https://jobs.example.test/search?q=GenAI+Engineer", "GET", True, custom
    )
    assert not discovery.signed_request(
        "https://jobs.example.test/jobs/view/42/?trk=x", "GET", True, custom
    )
    assert not discovery.signed_request(
        "https://jobs.example.test/viewjob?jk=abc", "GET", True, custom
    )


# --- a page's background POST -------------------------------------------------------------------

ALGOLIA = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Algolia%20for%20JavaScript&x-algolia-api-key=e2e&x-algolia-application-id=CSEKHVMS53"
POSTS = [
    (JUNGLE, ALGOLIA, "fetch"),
    (JUNGLE, ALGOLIA, "xhr"),
    (JUNGLE, "https://csekhvms53-1.algolianet.com/1/indexes/*/queries", "fetch"),
    (JUNGLE, "https://www.welcometothejungle.com/api/v1/search", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/li/track", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/graphql?queryId=voyagerJobsDashJobCards.1", "xhr"),
    (INDEED, "https://fr.indeed.com/m/rpc/jobsearch", "fetch"),
    ("http://www.welcometothejungle.test:8080/fr/jobs", "http://abc-dsn.algolia.test:8080/1/indexes/*/queries", "fetch"),
]  # fmt: skip
NOT_POSTS = [
    (JUNGLE, ALGOLIA, "ping"),  # a beacon
    (JUNGLE, ALGOLIA, "document"),
    (JUNGLE, ALGOLIA, "other"),
    (JUNGLE, ALGOLIA, ""),
    (JUNGLE, "https://algolia.net/1/indexes/*/queries", "fetch"),  # its hosts, not the domain
    (JUNGLE, "https://evil-algolia.net/1/indexes", "fetch"),
    (JUNGLE, "https://abc.algolia.net.example.test/1/indexes", "fetch"),
    (JUNGLE, "http://abc-dsn.algolia.net/1/indexes", "fetch"),
    (INDEED, ALGOLIA, "fetch"),  # another platform's search service
    (LINKEDIN, "https://elsewhere.example.test/api", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/feed/follows", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/messaging/conversations", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/relationships/invitations", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/growth/normInvitations?action=connect", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/jobs/jobPostings/42?action=save", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/jobs/42/easyApply", "fetch"),
    (LINKEDIN, "https://www.linkedin.com/voyager/api/graphql?action=execute&queryId=followCompany", "fetch"),
    (INDEED, "https://fr.indeed.com/rpc/savejob?jk=3f2a", "xhr"),
    (INDEED, "https://fr.indeed.com/rpc/bookmark", "xhr"),
    (INDEED, "https://fr.indeed.com/api/apply", "fetch"),
    (INDEED, "https://fr.indeed.com/logout", "fetch"),
    (INDEED, "https://secure.indeed.com/auth", "fetch"),
    ("https://jobs.example.test/search", "https://jobs.example.test/api/search", "fetch"),
]  # fmt: skip


@pytest.mark.parametrize(("start", "url", "kind"), POSTS)
def test_a_page_s_search_request_may_post_to_its_website(start, url, kind):
    assert discovery.signed_request(url, "POST", False, scope(start), kind=kind)


@pytest.mark.parametrize(("start", "url", "kind"), NOT_POSTS)
def test_anything_else_never_posts(start, url, kind):
    assert not discovery.signed_request(url, "POST", False, scope(start), kind=kind)


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_no_other_write_leaves(method):
    assert not discovery.signed_request(ALGOLIA, method, False, scope(JUNGLE), kind="fetch")


def test_a_document_never_posts_even_to_its_family():
    url = "https://www.welcometothejungle.com/fr/jobs"
    assert not discovery.signed_request(url, "POST", True, scope(JUNGLE), kind="document")


def test_the_person_s_own_words_in_a_query_are_read_as_a_search():
    words = discovery.Scope.parse(
        {"start": LINKEDIN, "titles": ["Social Media Follow Up"], "places": [], "filters": {},
         "signed_in": True}
    )  # fmt: skip
    url = "https://www.linkedin.com/voyager/api/search?keywords=Social%20Media%20Follow%20Up"
    assert discovery.signed_request(url, "POST", False, words, kind="fetch")
    assert not discovery.signed_request(url + "&do=follow", "POST", False, words, kind="fetch")


# --- the route and the tool's arguments ----------------------------------------------------------


def test_the_route_hands_the_request_s_kind_to_the_rule():
    tab = b17.tab_on(b17.Context([b17.Page()]))
    tab.request = SimpleNamespace(
        hosts=("welcometothejungle.com", "www.welcometothejungle.com"), purpose="sweep"
    )
    asyncio.run(tab.begin_discovery(scope(JUNGLE), "browser_navigate", {"url": JUNGLE}))
    for kind, sent in (("fetch", "sent"), ("ping", "blockedbyclient")):
        route = b17.Route()
        request = b17.request(ALGOLIA, "POST")
        request.resource_type = kind
        asyncio.run(tab._signed_route(route, request))
        assert route.outcome == sent


@pytest.mark.parametrize(
    ("name", "signed_in", "refused"),
    [
        ("browser_type", False, True),
        ("browser_click", True, True),
        ("browser_navigate", True, True),
        ("browser_type", True, False),
    ],
)
def test_enter_after_the_text_is_a_signed_in_typing_s_only(name, signed_in, refused):
    tab = b17.tab_on(b17.Context([b17.Page()]))
    raw = {"start": LINKEDIN, "titles": TITLES, "places": [], "filters": {}}
    if signed_in:
        raw["signed_in"] = True
    args = {"ref": "e1", "text": "GenAI Engineer", "submit": True, "_discovery": "native_get",
            "_discovery_scope": raw}  # fmt: skip
    answer = asyncio.run(b17.actions.run(tab, name, args))
    assert (answer.get("error") == "submit") is refused


# --- the outcome: the app's refusal wins ---------------------------------------------------------


def session(outcome: str = "", searches: int = 0, **refused: int):
    return SimpleNamespace(
        found=[],
        verified_count=0,
        outcome=outcome,
        searches={("GenAI Engineer", None): None} if searches else {},
        refused=__import__("collections").Counter(refused),
    )


@pytest.mark.parametrize(
    ("given", "code"),
    [
        (session("no_search_control", unsupported_interaction=1), "search_refused"),
        (session("no_list", unsupported_interaction=2), "search_refused"),
        (session("cookie_wall", unobserved_address=1), "search_refused"),
        (session("signin_wall", unsupported_interaction=1), "search_refused"),
        (session("app_refused"), "search_refused"),
        (session("app_refused", unsupported_interaction=1), "search_refused"),
        (session("no_search_control"), "no_search_box"),
        (session("no_list", model_value=3), "no_job_list"),  # the model's own slips are no refusal
        (session("no_match", searches=1, unsupported_interaction=1), None),  # searched: success
        (session("no_match", unsupported_interaction=1), "unsupported_interaction"),
        (session(unsupported_interaction=1), "unsupported_interaction"),
        (session(unobserved_address=1), "search_refused"),
        (session(model_value=1), "nothing_reported"),
        (session(), "nothing_reported"),
    ],
)
def test_the_app_s_refusal_wins_over_the_model_s_reason(given, code):
    assert interactive.outcome(given) == code


def test_the_model_may_say_the_app_refused_its_search():
    assert finder.NO_POSTINGS["app_refused"] == "search_refused"
    assert "app_refused" in finder.INTERACTIVE_PROMPT
    plain = finder.InteractiveScope(LINKEDIN, ("GenAI Engineer",))
    one = finder.FinderSession(None, LINKEDIN, "", "e2e-b19", interactive=plain)
    assert one.report_none("app_refused") == (
        "Noted. Report any posting you opened, then stop.",
        False,
    )
    assert one.outcome == "app_refused"
