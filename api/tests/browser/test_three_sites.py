# ruff: noqa: E501  tables of addresses, one per line
"""Find jobs on Welcome to the Jungle, LinkedIn and Indeed (sweep of 2026-10-30 on the Jetson).

1. Welcome to the Jungle, signed out, ended `page_failed` at once: browserd failed a Google Fonts
   file off the website (right) and then counted it as a stopped navigation, so the whole page
   answered `host_not_allowed`. Only the tab's own document is a navigation now
   (actions.Tab.navigation); a refused subresource is failed on its own. Signed out, the agent
   may also open the website's search page with one saved title in its search key
   (finder.InteractiveScope.search_address), as the site's scripts never run there.
2. LinkedIn, signed in: the click into its place box ("Ville, département ou code postal",
   pre-filled "Bordeaux et périphérie") and the click on a result card were both refused
   `unsupported_discovery`: no signed in rule took a click on a text box, nor on a plain
   container. A click into a search box now focuses it (discovery._signed_in_control), and a
   click on a result card is a click on its one posting link (discovery.card_posting,
   actions.card_link). The facts below are what FACTS_JS read in Chromium on a copy of
   LinkedIn's markup (a throwaway probe, never the real site).
3. Indeed, signed in, showed only Cloudflare's check: its frame and POSTs are let through,
   exactly (discovery.challenge), its frame keeps its Worker (actions.SIGNED_IN_JS), a sweep of
   Indeed starts Chromium with software WebGL (chromium.webgl), and the agent waits for a check
   and else reports `security_check`.
4. A refusal names its rule in browserd's warning, with the element's tag, role, box kind and
   the start of its name, never a typed value nor an address (actions.described).

Only the Playwright and CDP boundary is fake; no real site is requested.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder, finder_tools
from app.browser.sources import interactive
from tests.browser import test_b17_browserd as b17
from tests.browser import test_b19_families as families
from tests.browser.test_b19_search import control
from tests.browser.test_browserd_unit import request as lease_request
from tests.browser.test_j18_scope import discovery

actions = b17.actions
LINKEDIN = "https://www.linkedin.com/"
RESULTS = (
    "https://www.linkedin.com/jobs/search/?currentJobId=4012345678&geoId=106000000"
    "&keywords=GenAI%20Engineer&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON&refresh=true"
)
# A result card's title link as LinkedIn writes it, its tracking values invented.
POSTING = (
    "https://www.linkedin.com/jobs/view/4012345678/?eBP=CwEAAAGSzQ2fXk-vb3RjYXRlZ29yeV9pZF8xMjM0NQ"
    "&refId=Zm9vYmFyYmF6cXV4MTIzNA%3D%3D&trackingId=cXV4YmF6YmFyZm9vNDMyMQ%3D%3D"  # gitleaks:allow
    "&trk=flagship3_search_srp_jobs"
)
TITLES = ["genai engineer", "solution architect", "forward deployed engineer"]
JUNGLE = "https://www.welcometothejungle.com/fr/jobs"
INDEED = "https://fr.indeed.com/"


def scope(start: str = LINKEDIN, *, signed_in: bool = True, places=("everywhere",), filters=None):
    raw = {"start": start, "titles": TITLES, "places": list(places), "filters": filters or {}}
    return discovery.Scope.parse({**raw, **({"signed_in": True} if signed_in else {})})


# --- 1. a refused subresource is never a stopped navigation ---------------------------------------


def paused(found, url: str, kind: str | None, frame: str | None = "main") -> None:
    event: dict[str, Any] = {"requestId": "one", "request": {"method": "GET", "url": url}}
    if kind is not None:
        event["resourceType"] = kind
    if frame is not None:
        event["frameId"] = frame
    asyncio.run(found.intercept(event))


def jungle_tab(main: str | None = "main"):
    found, sent = families.tab(scope(JUNGLE, signed_in=False, places=()))
    found.request = SimpleNamespace(
        hosts=("welcometothejungle.com", "www.welcometothejungle.com"), purpose="sweep"
    )
    found.state.main_frame = main
    return found, sent


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://fonts.gstatic.com/s/inter/v13/inter.woff2", "Font"),
        ("https://fonts.googleapis.com/css2?family=Inter", "Stylesheet"),
        ("https://cdn.welcometothejungle.com/images/logo.png", "Image"),
        ("https://www.welcometothejungle.com/_next/image?url=x&w=64", "Image"),
        ("https://www.googletagmanager.com/gtm.js?id=GTM-0000", "Script"),
        ("https://csw.welcometothejungle.com/api/v1/x", "XHR"),
        ("https://www.welcometothejungle.com/api/v1/search?hitsPerPage=30", "Fetch"),
    ],
)
def test_a_refused_subresource_is_failed_and_the_page_goes_on(url, kind):
    found, sent = jungle_tab()
    paused(found, url, kind)
    assert sent == ["Fetch.failRequest"]
    assert not found.state.blocked


@pytest.mark.parametrize(
    ("url", "kind", "frame", "main", "stopped"),
    [
        ("https://elsewhere.example.test/", "Document", "main", "main", True),
        ("https://www.welcometothejungle.com/fr/me", "Document", "main", "main", True),
        ("https://ads.example.net/frame", "Document", "child", "main", False),  # another frame
        ("https://elsewhere.example.test/", "Document", "child", None, True),  # main unknown
        ("https://elsewhere.example.test/", "Document", None, "main", True),  # frame unknown
        ("https://elsewhere.example.test/", None, "main", "main", True),  # type unknown
        ("https://elsewhere.example.test/", "", "main", "main", True),
    ],
)
def test_only_the_tab_s_document_stops_the_page(url, kind, frame, main, stopped):
    found, sent = jungle_tab(main)
    paused(found, url, kind, frame)
    assert sent == ["Fetch.failRequest"]
    assert bool(found.state.blocked) is stopped


def test_a_document_of_the_list_goes_on_and_the_stop_names_its_kind(caplog):
    found, sent = jungle_tab()
    paused(found, "https://www.welcometothejungle.com/fr/jobs", "Document")
    paused(found, "https://fonts.gstatic.com/s/inter.woff2", "Font")
    assert sent == ["Fetch.continueRequest", "Fetch.failRequest"]
    assert not found.state.blocked
    paused(found, "https://elsewhere.example.test/next?token=e2e-41d7", "Document")
    found.on_list = lambda page: True
    with caplog.at_level("WARNING", logger="browserd"):
        answer = asyncio.run(found.settle({"ok": True}))
    assert answer["error"] == "host_not_allowed"
    [line] = [r.getMessage() for r in caplog.records if "allow list" in r.getMessage()]
    assert line.endswith("host=elsewhere.example.test kind=Document")
    assert "e2e-41d7" not in line
    assert "next" not in line


def test_the_anonymous_discovery_reads_its_main_frame_before_the_first_request():
    sent: list[str] = []

    class Client:
        def on(self, *args):
            pass

        async def send(self, name, args=None):
            sent.append(name)
            return {"frameTree": {"frame": {"id": "F1"}}} if name == "Page.getFrameTree" else {}

    page = b17.Page()

    class Context:
        def __init__(self) -> None:
            self.pages = [page]

        def on(self, *args):
            pass

        async def new_page(self):
            return page

        async def new_cdp_session(self, _):
            return Client()

    tab = b17.tab_on(b17.Context([b17.Page()]))

    async def new_context(**_):
        return Context()

    tab.browser.browser.new_context = new_context
    anonymous = discovery.Scope(b17.START, ("robot",))
    asyncio.run(tab.begin_discovery(anonymous, "browser_navigate", {"url": b17.START}))
    assert tab.state.main_frame == "F1"
    assert sent == ["Page.getFrameTree", "Fetch.enable"]


# --- 1b. signed out, a search by the search page's own address --------------------------------


def interactive_scope(start=JUNGLE, *, signed_in=False, places=(), filters=()):
    return finder.InteractiveScope(
        start, tuple(TITLES), tuple(places), filters, signed_in=signed_in
    )


@pytest.mark.parametrize(
    ("url", "places", "filters", "opens"),
    [
        ("https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer", (), (), True),
        ("https://www.welcometothejungle.com/fr/jobs?query=GenAI%20Engineer", (), (), True),
        ("https://www.welcometothejungle.com/fr/jobs?q=solution+architect&page=2", (), (), True),
        (
            "https://www.welcometothejungle.com/jobs?keywords=genai%20engineer&location=Paris",
            ("Paris",),
            (),
            True,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&location=",
            ("Paris",),
            (),
            True,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&location=Worldwide",
            ("everywhere",),
            (),
            True,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&remote=fulltime",
            (),
            (("remote", "fulltime"),),
            True,
        ),
        # Refused: no saved title, two search keys, another key, a value never saved, a saved
        # filter left out, a page that is no search page, a key twice.
        ("https://www.welcometothejungle.com/fr/jobs", (), (), False),  # no query: search_page's
        ("https://www.welcometothejungle.com/fr/jobs?query=", (), (), False),
        ("https://www.welcometothejungle.com/fr/jobs?query=data%20engineer", (), (), False),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&q=genai%20engineer",
            (),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&sortBy=date",
            (),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&location=Lyon",
            ("Paris",),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&location=Worldwide",
            ("Paris",),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer",
            (),
            (("remote", "fulltime"),),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&remote=partial",
            (),
            (("remote", "fulltime"),),
            False,
        ),
        ("https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&page=0", (), (), False),
        (
            "https://www.welcometothejungle.com/fr/settings/jobs?query=genai%20engineer",
            (),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/companies/acme/jobs?query=genai%20engineer",
            (),
            (),
            False,
        ),
        (
            "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer&query=genai%20engineer",
            (),
            (),
            False,
        ),
    ],
)
def test_signed_out_a_search_page_with_a_saved_title_opens(url, places, filters, opens):
    found = interactive_scope(places=places, filters=filters)
    assert found.search_address(finder.canonical(url)) is opens
    # Signed in the website's own box is the way (B19): never this address.
    assert not interactive_scope(places=places, filters=filters, signed_in=True).search_address(
        finder.canonical(url)
    )


def test_browserd_opens_the_same_search_address_signed_out():
    anonymous = scope(JUNGLE, signed_in=False, places=())
    for url in (
        "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer",
        "https://www.welcometothejungle.com/fr/jobs?q=solution+architect&page=2",
    ):
        assert discovery.read_url(url, JUNGLE, scope=anonymous)
    assert not discovery.read_url(
        "https://www.welcometothejungle.com/fr/jobs?query=data%20engineer", JUNGLE, scope=anonymous
    )


def test_the_agent_is_told_how_to_search_signed_out():
    prompt = " ".join(finder.INTERACTIVE_PROMPT.split())
    assert "When you are not signed in (`signed_in` false)" in prompt
    assert "/fr/jobs?query=genai%20engineer" in prompt


# --- 2. LinkedIn: the place box and a result card -------------------------------------------------

PLACE_BOX = control(
    "Ville, département ou code postal",
    "jobs-search-box-location-id-ember27",
    "Ville, département ou code postal",
    role="combobox",
    in_form=False,
)
PLACE_BOX_EN = control("City, state, or zip code", role="combobox", in_form=False)
TITLE_BOX = control(
    "Intitulé de poste, compétence ou entreprise",
    "jobs-search-box-keyword-id-ember27",
    role="combobox",
    in_form=False,
)
TITLE_LINK = control(
    "GenAI Engineer with verification",
    "ember260",
    "GenAI Engineer\nGenAI Engineer with verification",
    tag="a",
    kind="",
    href=POSTING,
    in_form=False,
)
CARD_TEXT = "GenAI Engineer\nGenAI Engineer with verification\nAcme Robotics\nBordeaux, Nouvelle-Aquitaine, France (Hybride)\nPromu(e)\nCandidature simplifiée\nx"
CARD = control(
    "ember250", CARD_TEXT, "Acme Robotics logo", "GenAI Engineer with verification", "ember260",
    "Ignorer l’offre GenAI Engineer", tag="li", kind="", in_form=False,
)  # fmt: skip


def judged(shape, tool="browser_click", text=None, start=LINKEDIN, **kw):
    args = {} if text is None else {"text": text}
    return discovery.judged(shape, tool, RESULTS, scope=scope(start, **kw), args=args)


@pytest.mark.parametrize("shape", [PLACE_BOX, PLACE_BOX_EN, TITLE_BOX])
def test_a_click_into_linkedin_s_search_boxes_is_allowed(shape):
    assert judged(shape) == (None, "point:signed_in")
    # Anonymous, the native rule holds: a script page's box is never clicked.
    assert judged(shape, signed_in=False)[0] == discovery.UNSUPPORTED


def test_the_place_box_then_takes_the_worldwide_name_and_the_title_box_does_not():
    assert judged(PLACE_BOX, "browser_type", "Worldwide")[0] is None
    assert judged(PLACE_BOX, "browser_type", "")[0] is None
    assert judged(TITLE_BOX, "browser_type", "Worldwide")[0] == discovery.UNSUPPORTED
    assert judged(TITLE_BOX, "browser_type", "GenAI Engineer")[0] is None


@pytest.mark.parametrize(
    ("shape", "code", "rule"),
    [
        # What refused each click on 2026-10-30, before this change, and what still refuses.
        (
            control("Write a message", role="combobox", in_form=False),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            control("Mot de passe", kind="password", in_form=False, credential=True),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            control(
                "Search",
                in_form=True,
                method="post",
                action="https://www.linkedin.com/jobs/search/",
            ),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            control(
                "Search",
                in_form=True,
                method="get",
                action="https://www.linkedin.com/jobs/view/1/apply",
            ),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            control(
                "Filtre Lieu : Bordeaux et périphérie",
                "searchFilter_location",
                tag="button",
                kind="button",
                in_form=False,
            ),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            CARD,
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),  # a card itself: actions.click retargets it
        (
            control("Easy Apply", tag="button", kind="button", in_form=False),
            "submit",
            "names_submit",
        ),
    ],
)
def test_every_other_click_is_refused_and_says_its_rule(shape, code, rule):
    assert judged(shape) == (code, rule)


def test_the_card_s_title_link_opens_the_posting_with_its_tracking_query():
    assert judged(TITLE_LINK) == (None, "point:signed_in")
    assert discovery.signed_request(POSTING, "GET", True, scope())  # and the document loads


LINKS_OK = [
    ([POSTING], 0),
    ([POSTING, POSTING], 0),  # the same posting twice (the title and the logo)
    (["https://www.linkedin.com/company/acme/life/", POSTING], 1),
    ([POSTING.replace("?", "?x=1&"), POSTING], 0),
    (["https://www.linkedin.com/jobs/view/4012345678"], 0),
    (["https://fr.indeed.com/rc/clk?jk=a1b2c3&from=serp"], None),  # another family
]
LINKS_NO = [
    [],
    ["https://www.linkedin.com/company/acme/"],
    [POSTING, "https://www.linkedin.com/jobs/view/4099999999/"],  # two postings: a list
    ["https://www.linkedin.com/jobs/view/4012345678/apply/"],
    ["https://www.linkedin.com/in/someone/"],
    ["https://elsewhere.example.test/jobs/view/4012345678/"],
    "not a list",
    [POSTING] * (discovery.CARD_LINKS + 1),
]


@pytest.mark.parametrize(("links", "index"), LINKS_OK)
def test_a_card_stands_for_its_one_posting_link(links, index):
    assert discovery.card_posting(links, scope()) == index


@pytest.mark.parametrize("links", LINKS_NO)
def test_a_card_with_no_posting_or_several_stands_for_nothing(links):
    assert discovery.card_posting(links, scope()) is None


def test_indeed_s_card_reads_its_job_key():
    indeed = scope(INDEED)
    links = [
        "https://fr.indeed.com/rc/clk?jk=a1b2c3&from=serp",
        "https://fr.indeed.com/viewjob?jk=a1b2c3",
    ]
    assert discovery.card_posting(links, indeed) is None  # two addresses of one job: two paths
    assert discovery.card_posting(links[:1] * 2, indeed) == 0
    other = ["https://fr.indeed.com/viewjob?jk=a1b2c3", "https://fr.indeed.com/viewjob?jk=d4e5f6"]
    assert discovery.card_posting(other, indeed) is None


@pytest.mark.parametrize(
    ("facts", "is_card"),
    [
        (CARD, True),
        (control("x", tag="div", kind="", role="button", in_form=False), True),
        (control("x", tag="article", kind="", role="article", in_form=False), True),
        (TITLE_LINK, False),
        (PLACE_BOX, False),
        (control("x", tag="button", kind="button", in_form=False), False),
        (control("x", tag="div", kind="", in_form=True), False),
        (control("x", tag="div", kind="", in_form=False, holds_submit=True), False),
        (control("x", tag="div", kind="", in_form=False, in_control=True), False),
        (control("x", tag="div", kind="", role="textbox", in_form=False), False),
        (control("x", tag="div", kind="", in_form=False, editable=True), False),
        (control("x", tag="body", kind="", in_form=False), False),
        (None, False),
    ],
)
def test_what_is_a_card(facts, is_card):
    assert discovery.card(facts) is is_card


class Links:
    def __init__(self, card: "Located") -> None:
        self.card = card

    def nth(self, index: int) -> "Located":
        return Located(self.card.links[index], self.card)


class Located:
    """A locator: a card holding `links`, or one of its links."""

    def __init__(self, href: str | None = None, card: "Located | None" = None, links=()):
        self.href, self.card, self.links, self.clicked = href, card, list(links), False

    async def evaluate(self, script: str, arg: Any = None, **_: Any) -> Any:
        return self.links if arg is not None else self.href

    def locator(self, selector: str) -> Links:
        assert selector == "a[href]"
        return Links(self)

    async def click(self, **_: Any) -> None:
        (self.card or self).clicked = f"link {self.href}" if self.card else "card"


def signed_in_tab():
    found, _ = families.tab(scope(), RESULTS)
    found.request = SimpleNamespace(hosts=("linkedin.com", "*.linkedin.com"), purpose="sweep")
    found.discovery = found.state.signed_in = True
    return found


def clicked(found, card: Located, monkeypatch) -> dict[str, Any]:
    async def element(args):
        return card, None

    async def nothing(*args, **kw):
        return None

    async def facts(locator, point=False):
        return CARD if locator is card else TITLE_LINK

    async def settle(result):
        return result

    async def here(tab):
        return {"ok": True, "url": RESULTS}

    found.element, found.approach, found.facts, found.settle = element, nothing, facts, settle
    monkeypatch.setattr(actions, "_here", here)
    return asyncio.run(actions.click(found, {"ref": "e40"}))


def test_a_click_on_a_result_card_clicks_its_posting_link(monkeypatch):
    card = Located(links=["https://www.linkedin.com/company/acme/", POSTING])
    result = clicked(signed_in_tab(), card, monkeypatch)
    assert result["ok"]
    assert result["clicked"] == "the card's posting link"
    assert card.clicked == f"link {POSTING}"


@pytest.mark.parametrize("links", LINKS_NO[:3])
def test_a_card_with_no_one_posting_link_is_refused_as_before(links, monkeypatch, caplog):
    card = Located(links=links)
    with caplog.at_level("WARNING", logger="browserd"):
        result = clicked(signed_in_tab(), card, monkeypatch)
    assert result["error"] == discovery.UNSUPPORTED
    assert card.clicked is False
    [line] = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert "rule=signed_in/native_form tag=li role=- type=- box=-" in line


def test_a_link_read_back_otherwise_is_never_clicked(monkeypatch):
    card = Located(links=[POSTING])

    class Elsewhere(Located):
        async def evaluate(self, script, arg=None, **_):
            return "https://www.linkedin.com/jobs/view/4099999999/" if arg is None else self.links

    elsewhere = Elsewhere(links=[POSTING])
    assert asyncio.run(actions.card_link(signed_in_tab(), elsewhere)) is not None
    elsewhere.locator = lambda selector: SimpleNamespace(nth=lambda i: Elsewhere(card=elsewhere))
    assert asyncio.run(actions.card_link(signed_in_tab(), elsewhere)) is None
    assert asyncio.run(actions.card_link(signed_in_tab(), card)) is not None


def test_anonymous_no_card_is_retargeted(monkeypatch):
    found = signed_in_tab()
    found.state.signed_in = False
    found.state.scope = scope(signed_in=False)
    card = Located(links=[POSTING])
    result = clicked(found, card, monkeypatch)
    assert result["error"] == discovery.UNSUPPORTED
    assert card.clicked is False


# --- 3. Indeed: Cloudflare's check, software WebGL, a check that never clears --------------------

CF = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/rcv/a1b2c/0x4AAAAAAADnPIDROrmt1Wwj/auto/fbE/new/normal/auto/"  # gitleaks:allow
CF_FLOW = (
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/flow/ov1/123:456:abc/8d1e/xyz"
)


@pytest.mark.parametrize(
    ("url", "method", "kind", "document", "main", "allowed"),
    [
        (CF, "GET", "document", True, False, True),  # its frame
        (
            "https://challenges.cloudflare.com/turnstile/v0/api.js",
            "GET",
            "script",
            False,
            False,
            True,
        ),
        (CF_FLOW, "POST", "xhr", False, False, True),
        (CF_FLOW, "POST", "fetch", False, False, True),
        (
            "https://fr.indeed.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=8d1e",
            "GET",
            "script",
            False,
            False,
            True,
        ),
        (
            "https://fr.indeed.com/cdn-cgi/challenge-platform/h/g/flow/ov1/123:456:abc/8d1e/xyz",
            "POST",
            "xhr",
            False,
            False,
            True,
        ),
        (
            "https://fr.indeed.com/jobs?q=genai+engineer&__cf_chl_f_tk=a1b2.c3",
            "POST",
            "document",
            True,
            True,
            True,
        ),
        ("https://fr.indeed.com/?__cf_chl_tk=a1b2", "POST", "document", True, True, True),
        # Refused: its host as the tab's document, a form POST, another host, a beacon, a
        # challenge path as a document, a POST of the tab without the challenge's key or to an
        # address the discovery never opens, http, another port, a user part.
        (CF, "GET", "document", True, True, False),
        (CF_FLOW, "POST", "beacon", False, False, False),
        (CF_FLOW, "POST", "document", True, False, False),
        (CF_FLOW, "PUT", "xhr", False, False, False),
        ("http://challenges.cloudflare.com/x", "GET", "script", False, False, False),
        ("https://challenges.cloudflare.com:8443/x", "GET", "script", False, False, False),
        ("https://user@challenges.cloudflare.com/x", "GET", "script", False, False, False),
        (
            "https://cloudflare.com/cdn-cgi/challenge-platform/x",
            "GET",
            "script",
            False,
            False,
            False,
        ),
        (
            "https://elsewhere.example.test/cdn-cgi/challenge-platform/x",
            "POST",
            "xhr",
            False,
            False,
            False,
        ),
        (
            "https://fr.indeed.com/cdn-cgi/challenge-platform/h/g/x",
            "GET",
            "document",
            True,
            False,
            False,
        ),
        ("https://fr.indeed.com/jobs?q=genai+engineer", "POST", "document", True, True, False),
        (
            "https://fr.indeed.com/jobs?q=data+engineer&__cf_chl_f_tk=a1",
            "POST",
            "document",
            True,
            True,
            False,
        ),
        ("https://fr.indeed.com/apply?__cf_chl_f_tk=a1", "POST", "document", True, True, False),
        (
            "https://fr.indeed.com/jobs?q=genai+engineer&__cf_chl_f_tk=a1",
            "POST",
            "document",
            True,
            False,
            False,
        ),
        (
            "https://fr.indeed.com/jobs?q=genai+engineer&__cf_chl_f_tk=a1",
            "POST",
            "xhr",
            False,
            False,
            False,
        ),
    ],
)
def test_exactly_cloudflare_s_check_is_let_through(url, method, kind, document, main, allowed):
    found = discovery.challenge(url, method, kind, scope(INDEED), document=document, main=main)
    assert found is allowed


def indeed_tab():
    found, _ = families.tab(scope(INDEED), INDEED)
    found.state.signed_in = True
    return found


def routed(found, url: str, method: str, kind: str, frame: str | None) -> str | None:
    route = b17.Route()
    req = b17.request(url, method, frame)
    req.resource_type = kind
    asyncio.run(found._signed_route(route, req))
    return route.outcome


def test_the_signed_in_route_lets_the_check_through_and_nothing_else_new():
    found = indeed_tab()
    assert routed(found, CF, "GET", "document", "sub") == "sent"
    assert routed(found, CF_FLOW, "POST", "xhr", None) == "sent"
    assert (
        routed(found, "https://fr.indeed.com/?__cf_chl_tk=a1", "POST", "document", "main") == "sent"
    )
    assert not found.state.blocked
    assert routed(found, CF, "GET", "document", "main") == "blockedbyclient"  # never the tab
    assert found.state.blocked
    found.state.blocked.clear()
    assert (
        routed(found, "https://ads.example.net/frame", "GET", "document", "sub")
        == "blockedbyclient"
    )
    assert (
        routed(found, "https://elsewhere.example.test/api", "POST", "xhr", None)
        == "blockedbyclient"
    )
    assert not found.state.blocked  # a frame or a background request is no navigation


def test_cloudflare_s_frame_alone_keeps_its_worker():
    script = actions.SIGNED_IN_JS
    assert script.index('location.origin === "https://challenges.cloudflare.com"') < script.index(
        "Worker"
    )
    assert "CHALLENGE_HOST" not in script


@pytest.mark.parametrize(
    ("purpose", "hosts", "on"),
    [
        ("sweep", ["indeed.com", "*.indeed.com"], True),
        ("sweep", ["indeed.test", "*.indeed.test"], True),
        ("sweep", ["linkedin.com", "*.linkedin.com"], False),
        ("sweep", ["welcometothejungle.com", "www.welcometothejungle.com"], False),
        ("sweep", ["jobs.example-robotics.test"], False),
        ("sweep", [], False),
        ("apply_fill", ["indeed.com", "*.indeed.com"], False),
        ("signin", [], False),
    ],
)
def test_software_webgl_only_for_a_sweep_of_indeed(purpose, hosts, on):
    from browserd import chromium
    from browserd.leases import Request

    request = Request.parse(
        {
            "user_id": "a",
            "profile_id": lease_request().profile_id,
            "uid": 20001,
            "purpose": purpose,
            "hosts": hosts,
        }
    )
    switches = chromium.switches(request, "/leases/x", 9333)
    webgl = [s for s in switches if "angle" in s or "swiftshader" in s]
    assert webgl == (list(chromium.WEBGL_SWITCHES) if on else [])
    assert switches[-2:] == ["--", "about:blank"]  # the address still comes last


def test_a_security_check_is_a_reason_the_agent_may_report():
    assert finder.NO_POSTINGS["security_check"] == "security_check"
    schema = finder_tools.SCHEMAS["report_no_postings"]["properties"]["reason"]["enum"]
    assert "security_check" in schema
    assert "security_check" in finder_tools.DESCRIPTIONS["report_no_postings"]
    prompt = " ".join(finder.INTERACTIVE_PROMPT.split())
    assert "wait for it to clear with browser_wait_for" in prompt
    assert "never click its checkbox or any CAPTCHA" in prompt
    assert "If it never clears, report security_check." in prompt


def outcome(reason: str | None, **refused: int) -> str | None:
    session = SimpleNamespace(
        found=[],
        verified_count=0,
        outcome=reason,
        searches={},
        refused={"unsupported_interaction": 0, "unobserved_address": 0, **refused},
    )
    return interactive.outcome(session)


def test_a_check_that_never_cleared_wins_over_a_refusal():
    assert outcome("security_check") == "security_check"
    assert outcome("security_check", unsupported_interaction=2) == "security_check"
    assert outcome("cookie_wall", unsupported_interaction=1) == "search_refused"  # as before


# --- 4. a refusal names its rule, never a typed value nor an address ------------------------------


def test_the_warning_names_the_element_and_never_its_text_or_address(caplog):
    found = signed_in_tab()
    composer = control(
        "Écrire un message à Jane Roe e2e-77aa", role="combobox", in_form=False, editable=True
    )
    with caplog.at_level(logging.WARNING, logger="browserd"):
        found.judge(composer, "type", "browser_type", {"text": "genai engineer"})
        found.judge(
            control(
                "Ville, département ou code postal https://x.test/?t=e2e-9c",
                tag="button",
                kind="button",
                in_form=False,
            ),
            "click",
            "browser_click",
        )
    first, second = [r.getMessage() for r in caplog.records]
    assert "browser_type refused: unsupported_discovery rule=composer" in first
    assert "name='(not logged)'" in first
    assert "Jane" not in first
    assert "genai" not in first
    assert "tag=button role=- type=button box=place" in second
    assert "e2e-9c" not in second
    assert "?" not in second


def test_every_refusal_of_a_discovery_says_its_rule(caplog):
    found = signed_in_tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        asyncio.run(actions.run(found, "browser_click", {"ref": "e1"}))  # no discovery marker
    assert any("rule=no_marker" in r.getMessage() for r in caplog.records)


def test_the_session_opens_the_search_address_signed_out_and_counts_no_refusal():
    from tests.browser.fakes import ScriptedFinder
    from tests.browser.test_j18_outcome import SEARCH, Jungle
    from tests.browser.test_multi_search import run
    from tests.browser.test_selected_interactive import selected

    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        # The saved remote stance goes with the title, as browserd's criteria want it.
        answers.append(await session.navigate("/fr/jobs?query=genai+engineer&remote=remote"))
        answers.append(await session.navigate("/fr/jobs?query=data+engineer&remote=remote"))
        session.report_none("no_list")
        return finder.Ran()

    site = Jungle()
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    opened, refused = answers
    assert opened == ("The page did not load.", True)  # the fake has no such page: it was asked
    address = "https://jobs.example.test/fr/jobs?query=genai+engineer&remote=remote"
    assert ("browser_navigate", address) in site.calls
    assert refused[0].startswith("Refused: open only an observed link")  # never a saved title
    assert entry["refused"] == {"unobserved_address": 1}


def test_a_page_of_a_search_that_did_not_load_says_why_without_its_query(monkeypatch, caplog):
    found = signed_in_tab()

    async def page():
        return found.state.page

    async def failed(*args, **kw):
        detail = f"Page.goto: net::ERR_ABORTED at {POSTING}"
        return {"ok": False, "error": "navigation_failed", "detail": detail}

    async def settle(result):
        return result

    async def nothing(*args, **kw):
        return None

    found.page, found.settle = page, settle
    found.pacer.dwell = nothing
    monkeypatch.setattr(actions.pages, "navigate", failed)
    with caplog.at_level(logging.WARNING, logger="browserd"):
        asyncio.run(actions.navigate(found, {"url": POSTING}))
    [line] = [r.getMessage() for r in caplog.records]
    assert line.endswith(
        "browser_navigate failed: navigation_failed net=net::ERR_ABORTED host=www.linkedin.com "
        "path=/jobs/view/4012345678/"
    )
    assert "trackingId" not in line
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        asyncio.run(actions.navigate(found, {"url": "https://www.linkedin.com/in/jane-roe-e2e/"}))
    [line] = [r.getMessage() for r in caplog.records]
    assert line.endswith("host=www.linkedin.com path=(not logged)")
