# ruff: noqa: E501  tables of addresses and card texts, one per line
"""Find jobs on LinkedIn and Indeed, round 2 (sweep 0c62430f of 2026-10-30, 14:25 UTC on the
Jetson, main e97a1029).

1. LinkedIn, signed in: three clicks were refused `signed_in/native_form`. The first was its place
   setting, a `div role=button` showing "Bordeaux et périphérie" (box kind place): it now opens
   the place box (discovery.place_opener), which then takes the worldwide name or nothing under
   the search box rules. The two others were result cards, `div role=button` elements of 155 and
   190 characters: a card of that role now stands for its one posting link
   (discovery.button_card, actions.click), and the click is dispatched on that link alone, judged
   at its own click point. A posting LinkedIn shows beside its list (`currentJobId`) may be opened
   at `/jobs/view/<id>/` (finder.shown_posting).
2. Indeed, signed in: Cloudflare's interstitial said "Navigateur non pris en charge". browserd now
   logs a tab's document that comes with `cf-mitigated: challenge` (host and status), each
   refusal or allowance of SIGNED_IN_JS (event, API, host, count) and the route's refusals of the
   check's requests (host, type, method); SIGNED_IN_JS lets Cloudflare's own interstitial build
   its Worker and read `navigator.serviceWorker` (proved in real Chromium by a throwaway probe,
   not here: the script's text is checked below). The agent is told that other addresses do not
   help while a check shows, and that signed in, an address with search terms is refused.

Only the Playwright and CDP boundary is fake; no real site is requested. Card texts and ids are
invented.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.browser import finder
from tests.browser import test_b17_browserd as b17
from tests.browser import test_b19_families as families
from tests.browser.test_b19_search import control
from tests.browser.test_j18_scope import discovery
from tests.browser.test_three_sites import (
    INDEED,
    LINKEDIN,
    POSTING,
    RESULTS,
    TITLE_LINK,
    Located,
    scope,
)

actions = b17.actions


def judged(shape, tool="browser_click", text=None, **kw):
    args = {} if text is None else {"text": text}
    return discovery.judged(shape, tool, RESULTS, scope=scope(**kw), args=args)


# --- 1a. LinkedIn's place setting opens the place box ----------------------------------------------

# What FACTS_JS reads on LinkedIn's place setting, as the refusal's warning described it: a div
# with the role button, its first name the place LinkedIn set (22 characters), a name that reads
# as a place box (its label or id).
PLACE_OPENER = control(
    "Bordeaux et périphérie", "Lieu", "jobs-search-location-ember31",
    tag="div", kind="", role="button", in_form=False,
)  # fmt: skip


def test_linkedin_s_place_setting_is_clicked_signed_in():
    assert len(PLACE_OPENER["names"][0]) == 22
    assert discovery.box_kind(PLACE_OPENER["names"]) == "place"
    assert discovery.place_opener(PLACE_OPENER)
    assert judged(PLACE_OPENER) == (None, "point:signed_in")
    # With specific places saved too: the place LinkedIn set is replaced by a saved one.
    assert judged(PLACE_OPENER, places=("Lyon",)) == (None, "point:signed_in")
    # Anonymous, the native rule holds.
    assert judged(PLACE_OPENER, signed_in=False)[0] == discovery.UNSUPPORTED


def opener(*names: str, **kw: Any) -> dict[str, Any]:
    return control(*names, **{"tag": "div", "kind": "", "role": "button", "in_form": False, **kw})


@pytest.mark.parametrize(
    ("shape", "code", "rule"),
    [
        # A place filter as a native button stays refused (the three websites change).
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
            opener("Bordeaux et périphérie", "Lieu", in_form=True),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            opener("Bordeaux et périphérie", "Lieu", in_control=True),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            opener("Bordeaux et périphérie", "Lieu", holds_submit=True),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (
            opener("Bordeaux et périphérie", "Lieu", editable=True),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        # Its names must read as a place box, not a title box nor neither.
        (opener("Bordeaux et périphérie"), discovery.UNSUPPORTED, "signed_in/native_form"),
        (
            opener("Bordeaux et périphérie", "Lieu", "Intitulé de poste"),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        # Action words, in a label or inside an id: never an opener.
        (
            opener("Créer une alerte pour cette ville"),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (opener("Follow this city"), discovery.UNSUPPORTED, "signed_in/native_form"),
        (opener("Share location"), discovery.UNSUPPORTED, "signed_in/native_form"),
        (
            opener("Bordeaux", "Lieu", "btnFollowLocation"),
            discovery.UNSUPPORTED,
            "signed_in/native_form",
        ),
        (opener("Report this location"), discovery.UNSUPPORTED, "signed_in/native_form"),
        (opener("Ville", "Candidature simplifiée"), discovery.UNSUPPORTED, "signed_in/native_form"),
        # A submit or a profile word wins first, a composer is never an opener.
        (opener("Enregistrer le lieu"), "submit", "names_submit"),
        (opener("Mettre à jour votre ville"), "submit", "names_submit"),
        (opener("Postuler à Bordeaux"), "submit", "names_submit"),
        (opener("Message to the city"), discovery.UNSUPPORTED, "signed_in/native_form"),
        (opener("Delete address"), "danger", "detector"),
    ],
)
def test_no_other_place_button_is_clicked(shape, code, rule):
    assert judged(shape) == (code, rule)


def test_the_opener_s_click_point_is_judged_too():
    covered = {
        **PLACE_OPENER,
        "point": {**control("Easy Apply", tag="button", kind="button"), "inside": False},
    }
    assert judged(covered)[0] == "submit"
    elsewhere = {
        **PLACE_OPENER,
        "point": {**control("Follow", tag="div", kind="", role="button"), "inside": False},
    }
    assert judged(elsewhere) == (discovery.UNSUPPORTED, "point:signed_in/native_form")


# The place box that opens, as LinkedIn's markup may hold it: a combobox in no form, or an input
# in a GET form whose action is the result list.
EDITOR = control("Ville, département ou code postal", role="combobox", in_form=False)
EDITOR_IN_FORM = control(
    "Ville, département ou code postal", "jobs-search-location-input",
    in_form=True, method="get", action="https://www.linkedin.com/jobs/search-results/",
)  # fmt: skip
EDITOR_IN_POST = control(
    "Ville, département ou code postal",
    in_form=True, method="post", action="https://www.linkedin.com/jobs/search-results/",
)  # fmt: skip


@pytest.mark.parametrize("editor", [EDITOR, EDITOR_IN_FORM])
def test_the_place_box_that_opens_takes_the_worldwide_name_or_nothing(editor):
    assert judged(editor, "browser_type", "Worldwide")[0] is None
    assert judged(editor, "browser_type", "Monde entier")[0] is None
    assert judged(editor, "browser_type", "")[0] is None
    assert judged(editor, "browser_type", "Lyon")[0] == discovery.UNSUPPORTED  # never saved
    assert judged(editor, "browser_type", "Lyon", places=("Lyon",))[0] is None
    # With only specific places saved, the worldwide name is refused.
    assert judged(editor, "browser_type", "Worldwide", places=("Lyon",))[0] == discovery.UNSUPPORTED
    assert judged(editor) == (None, "point:signed_in")  # and a click focuses it


def test_a_place_box_in_a_post_form_takes_nothing():
    assert judged(EDITOR_IN_POST, "browser_type", "Worldwide")[0] == discovery.UNSUPPORTED


@pytest.mark.parametrize(
    ("names", "acts"),
    [
        (["Bordeaux et périphérie", "Lieu"], False),
        (["jobs-search-location-ember31"], False),
        (["Saint-Étienne"], False),
        (["Créer une alerte"], True),
        (["btnShareCity"], True),
        (["Suivre la ville"], True),
        (["Se connecter"], True),
        ([None], True),
        ("not a list", True),
    ],
)
def test_what_acts(names, acts):
    assert discovery.acts(names) is acts


# --- 1b. LinkedIn's result cards with the role button ----------------------------------------------

# A LinkedIn result card as the refusal's warning described it: a div with the role button,
# its first name 155 characters of card text, with a nested Easy Apply label and a dismiss
# button's name; the company and the place invented.
CARD_TEXT = (
    "GenAI Engineer with verification GenAI Engineer with verification Acme Robotics "
    "Bordeaux, Nouvelle-Aquitaine, France (Hybride) Promu(e) Candidature simplifiée"
)
BUTTON_CARD = control(
    CARD_TEXT, "Acme Robotics logo", "Ignorer l’offre GenAI Engineer with verification",
    tag="div", kind="", role="button", in_form=False,
)  # fmt: skip


def test_the_card_text_is_as_long_as_the_sweep_s():
    assert 150 <= len(CARD_TEXT) <= 190
    # Judged on its own it is refused, as on 2026-10-30 (its Easy Apply label says submit).
    assert judged(BUTTON_CARD)[0] is not None


@pytest.mark.parametrize(
    ("facts", "is_card"),
    [
        (BUTTON_CARD, True),
        (control("x", tag="li", kind="", role="button", in_form=False), True),
        (control("x", tag="article", kind="", role="button", in_form=False), True),
        # A link role is not retargeted (no website showed one), nor any other control.
        (control("x", tag="div", kind="", role="link", in_form=False), False),
        (control("x", tag="span", kind="", role="button", in_form=False), False),
        (control("x", tag="button", kind="button", role="button", in_form=False), False),
        (control("x", tag="a", kind="", role="button", in_form=False), False),
        (control("x", tag="div", kind="", role="", in_form=False), False),  # discovery.card's
        (control("x", tag="div", kind="", role="button", in_form=True), False),
        (control("x", tag="div", kind="", role="button", in_form=False, in_control=True), False),
        (control("x", tag="div", kind="", role="button", in_form=False, holds_submit=True), False),
        (control("x", tag="div", kind="", role="button", in_form=False, editable=True), False),
        (control("x", tag="div", kind="", role="button", in_form=False, credential=True), False),
        (None, False),
    ],
)
def test_what_is_a_card_with_the_role_button(facts, is_card):
    assert discovery.button_card(facts) is is_card


def signed_in_tab():
    found, _ = families.tab(scope(), RESULTS)
    found.request = SimpleNamespace(hosts=("linkedin.com", "*.linkedin.com"), purpose="sweep")
    found.discovery = found.state.signed_in = True
    return found


def clicked(found, card: Located, card_facts: dict, link_facts: dict, monkeypatch) -> dict:
    async def element(args):
        return card, None

    async def nothing(*args, **kw):
        return None

    async def facts(locator, point=False):
        return card_facts if locator is card else link_facts

    async def settle(result):
        return result

    async def here(tab):
        return {"ok": True, "url": RESULTS}

    found.element, found.approach, found.facts, found.settle = element, nothing, facts, settle
    monkeypatch.setattr(actions, "_here", here)
    return asyncio.run(actions.click(found, {"ref": "e40"}))


def test_a_click_on_a_card_with_the_role_button_clicks_its_posting_link(monkeypatch):
    card = Located(links=["https://www.linkedin.com/company/acme/life/", POSTING])
    result = clicked(signed_in_tab(), card, BUTTON_CARD, TITLE_LINK, monkeypatch)
    assert result["ok"]
    assert result["clicked"] == "the card's posting link"
    assert card.clicked == f"link {POSTING}"  # the link, never the card


@pytest.mark.parametrize(
    ("name", "code", "rule"),
    [
        ("Candidature simplifiée", discovery.UNSUPPORTED, "point:signed_in/native_form"),
        ("Easy Apply", "submit", "point:names_submit"),
    ],
)
def test_a_nested_easy_apply_at_the_link_s_point_refuses_and_clicks_nothing(
    name, code, rule, monkeypatch, caplog
):
    # The posting link's centre is covered by the card's Easy Apply button.
    easy = control(name, tag="button", kind="button", in_form=False)
    covered = {**TITLE_LINK, "point": {**easy, "inside": False}}
    card = Located(links=[POSTING])
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = clicked(signed_in_tab(), card, BUTTON_CARD, covered, monkeypatch)
    assert result["error"] == code
    assert card.clicked is False
    [line] = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert f"rule={rule} tag=a " in line  # the link was judged, never the card


@pytest.mark.parametrize(
    "links",
    [
        [],
        ["https://www.linkedin.com/company/acme/"],
        [POSTING, "https://www.linkedin.com/jobs/view/4099999999/?trk=flagship3_search_srp_jobs"],
    ],
)
def test_a_card_with_the_role_button_and_no_one_posting_is_refused(links, monkeypatch, caplog):
    card = Located(links=links)
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = clicked(signed_in_tab(), card, BUTTON_CARD, TITLE_LINK, monkeypatch)
    assert result["error"] is not None
    assert card.clicked is False
    [line] = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert "tag=div role=button" in line


def test_a_card_with_the_role_link_is_never_retargeted(monkeypatch):
    link_card = control(CARD_TEXT, tag="div", kind="", role="link", in_form=False)
    card = Located(links=[POSTING])
    result = clicked(signed_in_tab(), card, link_card, TITLE_LINK, monkeypatch)
    assert result["error"] is not None
    assert card.clicked is False


def test_the_place_setting_with_no_link_is_clicked_itself(monkeypatch):
    # LinkedIn's place setting is a div with the role button too: with no posting link it is
    # judged on its own, and the place opener rule takes it.
    place = Located(links=[])
    result = clicked(signed_in_tab(), place, PLACE_OPENER, TITLE_LINK, monkeypatch)
    assert result["ok"]
    assert place.clicked == "card"
    assert "clicked" not in result


def test_anonymous_no_card_with_the_role_button_is_retargeted(monkeypatch):
    found = signed_in_tab()
    found.state.signed_in = False
    found.state.scope = scope(signed_in=False)
    card = Located(links=[POSTING])
    result = clicked(found, card, BUTTON_CARD, TITLE_LINK, monkeypatch)
    assert result["error"] is not None
    assert card.clicked is False


# --- 1c. the posting LinkedIn shows beside its list ------------------------------------------------


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        (RESULTS, "https://www.linkedin.com/jobs/view/4012345678/"),
        (
            "https://www.linkedin.com/jobs/search-results/?currentJobId=4012345678&keywords=GenAI%20Engineer",
            "https://www.linkedin.com/jobs/view/4012345678/",
        ),
        (
            "https://fr.linkedin.com/jobs/collections/recommended/?currentJobId=42",
            "https://fr.linkedin.com/jobs/view/42/",
        ),
        (
            "http://www.linkedin.test:8080/jobs/search/?currentJobId=42",
            "http://www.linkedin.test:8080/jobs/view/42/",
        ),
        ("https://www.linkedin.com/jobs/search/?keywords=GenAI", ""),
        ("https://www.linkedin.com/jobs/search/?currentJobId=42&currentJobId=43", ""),
        ("https://www.linkedin.com/jobs/search/?currentJobId=42abc", ""),
        ("https://www.linkedin.com/jobs/search/?currentJobId=../../in/jane", ""),
        ("https://www.linkedin.com/jobs/search/?currentJobId=" + "1" * 21, ""),
        ("https://fr.indeed.com/jobs?q=genai&currentJobId=42", ""),
        ("https://elsewhere.example.test/jobs?currentJobId=42", ""),
        ("", ""),
    ],
)
def test_the_posting_shown_beside_the_list(url, shown):
    assert finder.shown_posting(url) == shown


def test_the_posting_shown_beside_the_list_opens_by_its_address():
    plain = finder.InteractiveScope(LINKEDIN, ("GenAI Engineer",), signed_in=True)
    one = finder.FinderSession(None, LINKEDIN, "", "e2e-round2", interactive=plain)
    one._read(RESULTS, "")
    posting = "https://www.linkedin.com/jobs/view/4012345678/"
    assert posting in one.seen
    assert "https://www.linkedin.com/jobs/view/4099999999/" not in one.seen
    # Another posting's address stays an unobserved address, refused before the browser.
    answer = asyncio.run(one.navigate("/jobs/view/4099999999/"))
    assert answer == (finder.UNOBSERVED_SIGNED_IN, True)
    # browserd opens the shown posting's page as any posting of the family.
    assert discovery.signed_request(posting, "GET", True, scope())


def test_the_agent_is_told_how_to_open_the_shown_posting_and_the_place_setting():
    prompt = " ".join(finder.INTERACTIVE_PROMPT.split())
    assert "currentJobId=<number>, open /jobs/view/<number>/ on the same host" in prompt
    assert "click it to open the place box, then clear it or type in it" in prompt
    assert "opens the place box from the button showing the place" in prompt


# --- 2. Indeed: what the next run will say of Cloudflare's check ----------------------------------


def indeed_tab():
    found, _ = families.tab(scope(INDEED), INDEED)
    found.state.signed_in = True
    found.lease_tag = "e2e-l1"
    return found


def response(url: str, status: int, headers: dict, *, frame: str | None = "main") -> Any:
    request = b17.request(url, "GET", frame)
    return SimpleNamespace(url=url, status=status, headers=headers, request=request)


def test_a_tab_s_document_that_is_cloudflare_s_check_is_logged(caplog):
    found = indeed_tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        found._response(response(f"{INDEED}?q=e2e-secret", 403, {"cf-mitigated": "challenge"}))
    [line] = [r.getMessage() for r in caplog.records]
    assert line == "lease e2e-l1: the page is a security check: host=fr.indeed.com status=403"
    assert found.state.challenged
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        # A frame's document or a page without the header says nothing, and the page that
        # follows the check clears the mark.
        found._response(response(INDEED, 403, {"cf-mitigated": "challenge"}, frame="sub"))
        found._response(response(INDEED, 200, {}))
    assert not caplog.records
    assert not found.state.challenged
    found._response(SimpleNamespace())  # unreadable: nothing, no exception
    assert not found.state.challenged


def console(text: str, page: str = INDEED) -> Any:
    """A console line of the page at `page` (round 3: the host logged is the page's own)."""
    return SimpleNamespace(text=text, page=SimpleNamespace(url=page))


def test_what_the_page_script_refused_is_counted_and_logged(caplog):
    found = indeed_tab()
    mark = actions.SCRIPT_MARK
    with caplog.at_level(logging.WARNING, logger="browserd"):
        for _ in range(5):
            found._console(console(f"{mark} blocked Worker https://fr.indeed.com"))
        found._console(console(f"{mark} allowed Worker https://fr.indeed.com"))
        found._console(console(f"{mark} blocked serviceWorker https://fr.indeed.com"))
        # Page text, another format, an origin that is no host: never logged as such; the
        # host is the page's own, never the line's (round 3), so this counts as the 6th.
        found._console(console("Jane Roe e2e-77aa says hello"))
        found._console(console(f"{mark} blocked Worker https://fr.indeed.com extra words"))
        found._console(console(f"{mark} blocked Wor-ker https://fr.indeed.com"))
        found._console(console(f"{mark} blocked Worker https://e2e-jane@x.test/?q=secret"))
        found._console(SimpleNamespace())
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "lease e2e-l1: the search's page script blocked Worker: host=fr.indeed.com count=1",
        "lease e2e-l1: the search's page script blocked Worker: host=fr.indeed.com count=2",
        "lease e2e-l1: the search's page script blocked Worker: host=fr.indeed.com count=4",
        "lease e2e-l1: the search's page script allowed Worker: host=fr.indeed.com count=1",
        "lease e2e-l1: the search's page script blocked serviceWorker: host=fr.indeed.com count=1",
    ]
    for word in ("Jane", "secret", "e2e-jane", "extra"):
        assert all(word not in line for line in lines)


def test_the_counts_are_bounded_and_end_with_the_lease():
    found = indeed_tab()
    for index in range(actions.SCRIPT_LOGGED + 10):
        page = f"https://h{index}.test/"
        found._console(console(f"{actions.SCRIPT_MARK} blocked Worker https://x.test", page))
    assert len(found.state.script_counts) == actions.SCRIPT_LOGGED
    found.state.challenged, found.state.route_logged = True, 3
    found.state.end()
    assert not found.state.script_counts
    assert not found.state.challenged
    assert found.state.route_logged == 0


def routed(found, url: str, method: str, kind: str, frame: str | None) -> str | None:
    route = b17.Route()
    req = b17.request(url, method, frame)
    req.resource_type = kind
    asyncio.run(found._signed_route(route, req))
    return route.outcome


def test_the_route_s_refusals_are_logged_and_say_the_check(caplog):
    found = indeed_tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        # A beacon of the check (refused: only a fetch or an XHR posts), with a query.
        assert (
            routed(
                found,
                "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/report?ray=e2e",
                "POST",
                "beacon",
                None,
            )
            == "blockedbyclient"
        )
        # Off the check, a refused frame or request is logged too, as no check's ...
        assert (
            routed(found, "https://ads.example.net/pixel?u=e2e", "GET", "image", "sub")
            == "blockedbyclient"
        )
        # ... and while the tab's document is the check, every refused request is the check's.
        found.state.challenged = True
        assert (
            routed(found, "https://elsewhere.example.test/x?u=e2e", "POST", "xhr", None)
            == "blockedbyclient"
        )
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "lease e2e-l1: a request refused: host=challenges.cloudflare.com kind=beacon method=POST check=1",
        "lease e2e-l1: a request refused: host=ads.example.net kind=image method=GET check=0",
        "lease e2e-l1: a request refused: host=elsewhere.example.test kind=xhr method=POST check=1",
    ]
    assert all("e2e" not in line.split("lease e2e-l1")[1] for line in lines)


def test_the_route_s_refusals_logged_are_bounded():
    found = indeed_tab()
    found.state.challenged = True
    for _ in range(actions.ROUTE_LOGGED + 5):
        routed(found, "https://elsewhere.example.test/x", "POST", "xhr", None)
    assert found.state.route_logged == actions.ROUTE_LOGGED


def test_a_signed_in_discovery_listens_to_responses_and_the_console():
    events: list[str] = []

    class Context:
        pages: ClassVar[list] = []

        async def add_init_script(self, script):
            events.append("script")

        async def route(self, pattern, handler):
            events.append("route")

        def on(self, name, handler):
            events.append(name)

        async def new_page(self):
            return SimpleNamespace(url="about:blank")

        async def new_cdp_session(self, page):
            async def send(name, args=None):
                events.append(name)

            return SimpleNamespace(send=send)

    found = indeed_tab()
    found.state.scope = None
    found.browser = SimpleNamespace(browser=SimpleNamespace(contexts=[Context()]))
    asyncio.run(found._begin_signed_in(scope(INDEED)))
    assert {"script", "route", "request", "response", "console"} <= set(events)


@pytest.mark.parametrize(
    ("url", "related"),
    [
        ("https://challenges.cloudflare.com/turnstile/v0/api.js", True),
        ("https://fr.indeed.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1", True),
        ("https://fr.indeed.com/jobs?q=x", False),
        ("https://cdn-cgi.example.test/challenge-platform/", False),
        (None, False),
        ("http://[::1", False),
    ],
)
def test_what_is_a_request_of_the_check(url, related):
    assert actions.challenge_request(url) is related


def test_the_page_script_lets_only_cloudflare_s_interstitial_build_its_worker():
    script = actions.SIGNED_IN_JS
    # The check's frame keeps everything; the site's pages keep none of it.
    assert script.index('location.origin === "https://challenges.cloudflare.com"') < script.index(
        "Worker"
    )
    for placeholder in ("CHALLENGE_HOST", "CHALLENGE_PATH", "SCRIPT_MARK"):
        assert placeholder not in script
    # The interstitial: the top frame, its own response status 403 or 503, Cloudflare's options.
    assert 'performance.getEntriesByType("navigation")' in script
    assert "responseStatus" in script
    assert "window.top === window" in script
    assert "status === 403 || status === 503" in script
    assert "self._cf_chl_opt" in script
    # Only the Worker, from a blob: address of its origin or the check's own path.
    # (round 3: through the URL getters captured before any page script runs)
    assert 'protocolOf(at) === "blob:"' in script
    assert 'startsWith(pathOf(at), "/cdn-cgi/challenge-platform/")' in script
    assert 'name === "Worker" && interstitial' in script
    # Registering a service worker stays refused everywhere; elsewhere it is removed.
    assert '"register"' in script
    assert "delete Navigator.prototype.serviceWorker" in script
    assert f"{actions.SCRIPT_MARK} " in script


def test_the_agent_is_told_a_check_is_not_left_by_another_address():
    prompt = " ".join(finder.INTERACTIVE_PROMPT.split())
    assert "Opening another address of the website does not help while a check shows" in prompt
    assert "an address holding search terms (such as /jobs?q=...) is refused" in prompt
    # What round 1 told stays.
    assert "never click its checkbox or any CAPTCHA" in prompt
    assert "If it never clears, report security_check." in prompt


# --- 3. Welcome to the Jungle's cookie banner (run 3, 14:42, signed in) ----------------------------

JUNGLE = "https://www.welcometothejungle.com/fr/jobs"
JUNGLE_PAGE = "https://www.welcometothejungle.com/fr/jobs?query=genai%20engineer"
BANNER_FORM = ["input:checkbox", "input:checkbox", "button:submit", "button:button"]


def banner_button(*names: str, kinds=("button:submit",), **kw: Any) -> dict[str, Any]:
    """The banner's decline button as FACTS_JS reads it: a submit button of the banner's form,
    an Axeptio style id beside its label."""
    given = {"method": "get", "action": JUNGLE_PAGE, "kind": "submit", **kw}
    return control(
        *(names or ("Close without accepting cookies",)), "axeptio_btn_dismiss",
        tag="button", in_form=True, form_kinds=list(kinds), **given,
    )  # fmt: skip


def jungle_judged(shape, *, signed_in=True):
    s = scope(JUNGLE, signed_in=signed_in, places=())
    return discovery.judged(shape, "browser_click", JUNGLE_PAGE, scope=s)


@pytest.mark.parametrize(
    "shape",
    [
        banner_button(),
        banner_button(kinds=BANNER_FORM),
        banner_button(kinds=[*BANNER_FORM, "fieldset:fieldset", "input:radio", "input:reset"]),
        banner_button(method="post"),  # its own page: whatever it then sends, the route holds
        banner_button(method="dialog", action=""),
        banner_button(action=""),  # read as the page itself
        banner_button("Fermer sans accepter"),
        banner_button("Fermer sans accepter les cookies"),
        banner_button("Continuer sans accepter les cookies"),
        banner_button("Refuser et fermer"),
        banner_button("close without accepting"),
        banner_button("Continue without accepting cookies"),
    ],
)
def test_the_banner_s_decline_submit_in_a_consent_form_closes_it(shape):
    assert discovery.decline(shape, JUNGLE_PAGE)
    assert jungle_judged(shape) == (None, "decline")
    assert jungle_judged(shape, signed_in=False) == (None, "decline")


@pytest.mark.parametrize(
    "shape",
    [
        # A form that carries anything of the person's, or more controls than read.
        *(
            banner_button(kinds=["button:submit", kind])
            for kind in (
                "input:text",
                "input:email",
                "input:password",
                "input:file",
                "input:hidden",
                "input:search",
                "textarea:textarea",
                "select:select-one",
                "div:editable",
                "output:output",
                "object:",
            )
        ),
        banner_button(kinds=["button:submit"] * discovery.CONSENT_FIELDS),
        # No form_kinds read.
        control(
            "Close without accepting cookies",
            tag="button",
            kind="submit",
            in_form=True,
            method="get",
            action=JUNGLE_PAGE,
        ),
        # A form that submits elsewhere, or to an address that signs out or applies.
        banner_button(action="https://consent.example.net/collect"),
        banner_button(action="https://www.welcometothejungle.com/fr/logout"),
        banner_button(action="https://www.welcometothejungle.com/fr/jobs/42/apply"),
        banner_button(method="put"),
        # Its names: never a decline but one of the phrases, made only of neutral words beside.
        banner_button("Close"),
        banner_button("Accept and close"),
        banner_button("Close without accepting cookies", "Apply now"),
        banner_button("Fermer sans accepter", "Postuler"),
        banner_button("Close without accepting cookies", "Save my choices"),
        # Never an image submit, nor a submit holding another.
        banner_button(kind="image"),
        banner_button(holds_submit=True),
    ],
)
def test_no_other_submit_button_is_a_decline(shape):
    assert not discovery.decline(shape, JUNGLE_PAGE)
    assert jungle_judged(shape)[0] is not None


def test_a_consent_form_needs_the_page_s_address():
    assert not discovery.decline(banner_button(action=""))  # no address to tell its target
    assert discovery.decline(banner_button(method="dialog", action=""))


def test_a_decline_click_is_marked_declined(monkeypatch):
    found, _ = families.tab(scope(JUNGLE, places=()), JUNGLE_PAGE)
    found.request = SimpleNamespace(
        hosts=("welcometothejungle.com", "www.welcometothejungle.com"), purpose="sweep"
    )
    found.discovery = found.state.signed_in = True
    button = Located(links=[])
    result = clicked(found, button, banner_button(kinds=BANNER_FORM), TITLE_LINK, monkeypatch)
    assert result["ok"]
    assert result["declined"] is True  # the api counts no search for it
    assert button.clicked == "card"


class TimeoutError(Exception):  # Playwright's TimeoutError is matched by its name
    pass


def not_ready(message: str, point: Any, *, discovery_on=True):
    found = signed_in_tab()
    found.discovery = discovery_on

    async def element(args):
        return Located(), None

    async def approach(locator):
        raise TimeoutError(message)

    async def facts(locator, point=False):
        return {**PLACE_OPENER, "point": shown}

    shown = point
    found.element, found.approach, found.facts = element, approach, facts
    return asyncio.run(actions.click(found, {"ref": "e7"}))


def test_a_click_on_an_element_behind_a_layer_says_so(caplog):
    overlay = {**control("x", tag="div", kind="", role="dialog"), "inside": False}
    message = (
        'Timeout 5000ms exceeded. <div class="e2e-secret">Jane</div> intercepts pointer events'
    )
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready(message, overlay)
    assert result["error"] == "element_covered"
    [line] = [r.getMessage() for r in caplog.records]
    assert line.endswith("browser_click not ready: reason=covered covered_by=div/dialog")
    assert "Jane" not in line
    assert "secret" not in line


def test_a_transparent_layer_is_told_by_where_the_click_lands(caplog):
    layer = {**control("", tag="div", kind=""), "inside": False}
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready("Timeout 5000ms exceeded. element is not stable", layer)
    assert result["error"] == "element_covered"
    assert caplog.records[0].getMessage().endswith("reason=moving covered_by=div/-")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Timeout 5000ms exceeded. element is not visible", "not_visible"),
        ("Timeout 5000ms exceeded. element is outside of the viewport", "outside_viewport"),
        ("Timeout 5000ms exceeded.", "unknown"),
    ],
)
def test_an_element_that_never_gets_ready_says_why(message, reason, caplog):
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready(message, None)
    assert result == actions.failure("page_error", actions.NOT_READY)
    assert caplog.records[0].getMessage().endswith(f"reason={reason} covered_by=-")


def test_outside_a_discovery_a_timeout_still_raises():
    with pytest.raises(TimeoutError):
        not_ready("element is not visible", None, discovery_on=False)


def test_the_failure_log_names_why_in_the_code_s_words():
    assert actions.actionability("x intercepts pointer events") == "covered"
    assert actions.actionability("Jane Roe e2e") == "unknown"
