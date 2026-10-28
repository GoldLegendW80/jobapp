"""A cookie banner is closed by declining it, and only that (2026-10-28, issue 716): Welcome to
the Jungle's "C is for COOKIE" banner covered its search page, and browserd refused every click
of a discovery that was no native search, so the agent could not close it. browserd now takes
one more click in a discovery: a button whose name is a decline phrase (DECLINES), every other
name of it made only of neutral words (NEUTRAL) in basic Latin letters, never a link nor a form's
submit button, at its click point too. Accepting stays refused. The search page itself, its
address with no query (`/fr/jobs`), opens; a search request still carries a saved title. A
click Playwright could not make because another element covers it is told as `element_covered`.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import discovery, routes

from tests.browser.test_b5_actions import facts

START = "https://www.example-jungle.test/fr/jobs"
SCOPE = discovery.Scope(START, ("genai engineer",), ("Paris",), (("contract", "CDI"),))


def button(*names: str, tag: str = "button", kind: str = "button", **more: Any) -> dict:
    shape = facts(*names, tag=tag, kind=kind, in_form=False, **more)
    shape["discovery"] = {
        "native": False,
        "method": "",
        "action": "",
        "target": "",
        "name": "",
        "href": "",
        "fields": [],
    }
    shape["point"] = {**{k: v for k, v in shape.items() if k != "point"}, "inside": True}
    return shape


def click(shape: dict, scope: discovery.Scope | None = SCOPE) -> str | None:
    return discovery.verdict(shape, "browser_click", START, scope=scope)


DECLINED = [
    ("No, thanks",),
    ("NO THANKS",),
    ("No thank you",),
    ("Reject all",),
    ("Reject All Cookies",),
    ("Decline",),
    ("Refuser",),
    ("Tout refuser",),
    ("Continuer sans accepter",),
    ("Continuer sans accepter →",),
    ("Je refuse",),
    ("Only necessary cookies",),
    ("Uniquement les cookies nécessaires",),
    ("Ablehnen",),
    ("Alle ablehnen",),
    ("axeptio_btn_dismiss", "No, thanks"),
    ("didomi-notice-disagree-button", "Disagree"),
    ("onetrust-reject-all-handler", "Reject All"),
    ("CybotCookiebotDialogBodyButtonDecline", "Decline"),
    ("No, thanks", "Close the banner"),
]


@pytest.mark.parametrize("names", DECLINED)
@pytest.mark.parametrize("signed_in", [False, True])
def test_a_decline_button_of_a_cookie_banner_may_be_clicked(names, signed_in):
    scope = SCOPE if not signed_in else discovery.Scope(START, ("genai engineer",), signed_in=True)
    assert click(button(*names), scope) is None
    assert click(button(*names), None) is None  # with no scope too: the rule is the element's


ACCEPTED = [
    ("OK for me",),
    ("I choose",),
    ("Accept all",),
    ("Accepter et fermer",),
    ("J'accepte",),
    ("Tout accepter",),
    ("Agree",),
    ("Allow all",),
    ("Customize",),
    ("Personnaliser",),
    ("Paramétrer les cookies",),
    ("Cookie settings",),
    ("Préférences",),
    ("Yes",),
    ("Oui",),
    ("D'accord",),
    ("Accept only necessary",),  # says accept: not a phrase of the list (fail closed)
    ("No",),
    ("Close",),
    ("Fermer",),
    ("Dismiss",),
    ("Refuser les offres",),
    ("Reject application",),
    ("No, thanks", "OK for me"),  # a wrapper of both buttons
    ("No, thanks", "I choose"),
    ("axeptio_btn_acceptAll", "No, thanks"),
    ("accept-cookies", "Refuser"),
    ("No, thanks", "Allow"),
    ("Sign in", "No, thanks"),
    ("Se connecter", "Refuser"),
    ("signinBanner", "No thanks"),
    ("Create my profile", "No, thanks"),
    ("Apply", "Refuser"),
    ("Send", "Decline"),
    ("Supprimer", "Refuser"),
    (),
    ("",),
]


@pytest.mark.parametrize("names", ACCEPTED)
def test_any_other_banner_button_stays_refused(names):
    assert click(button(*names)) is not None, names


# Names probed beside "No, thanks" in the review of PR 720 (round 2): an allow list refuses
# every one, in any language or script, a homoglyph included (a Cyrillic letter in "Accept").
BESIDE_A_DECLINE = [
    "S'abonner", "Créer mon profil", "Créer un compte", "Devenir membre", "Become member", "Join",
    "Consent", "Consentir", "Je consens", "Einverstanden", "Aceptar", "Accetta", "Aceitar",
    "Akceptuję", "同意", "A\u0441cept all", "Se déconnecter", "Follow", "Créer une alerte",
    "Turn on job alerts", "Upload CV", "Déposer mon CV", "Refuser et s'abonner",
    "Gérer mes choix", "Valider mes choix", "Continue", "Got it", "OK", "Subscribe",
    "jungle_btn_dismiss", "CookieAccEpt", "Close and accept",
]  # fmt: skip


@pytest.mark.parametrize("other", BESIDE_A_DECLINE)
@pytest.mark.parametrize("signed_in", [False, True])
def test_a_decline_name_beside_any_other_word_stays_refused(other, signed_in):
    scope = SCOPE if not signed_in else discovery.Scope(START, ("genai engineer",), signed_in=True)
    for names in (("No, thanks", other), (other, "Refuser"), ("Reject all", other, "Close")):
        assert click(button(*names), scope) is not None, names
    shape = button("No, thanks")
    shape["point"]["names"] = ["No, thanks", other]  # at the click point too
    assert click(shape, scope) is not None, other


@pytest.mark.parametrize(
    "phrase",
    [
        "R\u0435ject all",  # a Cyrillic small ie
        "Reje\u0441t all",  # a Cyrillic small es
        "\u039do, thanks",  # a Greek capital nu
        "\u62d2\u5426",
        "\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c",
    ],
)
def test_a_decline_phrase_in_another_script_stays_refused(phrase):
    assert click(button(phrase)) is not None, phrase


def test_a_full_width_decline_reads_as_its_latin_letters():
    assert click(button("\uff32eject all")) is None  # NFKC: the full width R is an R


@pytest.mark.parametrize(
    ("changes", "point"),
    [
        ({"tag": "a"}, None),  # a link: the link rule judges it
        ({"discovery_href": "https://www.example-jungle.test/fr/consent"}, None),
        ({"tag": "input", "type": "text"}, None),
        ({"tag": "div", "role": ""}, None),  # no button
        ({"tag": "select"}, None),
        ({"in_form": True, "type": "submit"}, None),  # it would submit its form
        ({"holds_submit": True}, None),
        ({"selector_hit": True}, None),
        ({"selector_error": True}, None),
        ({"custom_submit": True}, None),
        ({"editable": True}, None),
        ({"credential": True}, None),
        ({"discovery_target": "_blank"}, None),
        ({}, {"inside": False}),  # the click lands on something else
        ({}, {"names": ["OK for me"]}),  # the point is the accepting button
        ({}, "none"),  # no element at the point
    ],
)
def test_a_decline_button_of_another_shape_or_covered_stays_refused(changes, point):
    shape = button("No, thanks")
    for key, value in changes.items():
        if key.startswith("discovery_"):
            shape["discovery"][key.removeprefix("discovery_")] = value
        else:
            shape[key] = value
    shape["point"] = {**{k: v for k, v in shape.items() if k != "point"}, "inside": True}
    if point == "none":
        shape["point"] = None
    elif point:
        shape["point"].update(point)
    assert click(shape) is not None


@pytest.mark.parametrize(
    ("tag", "kind", "role", "in_form"),
    [
        ("button", "button", "", True),  # type=button in a form: submits nothing
        ("button", "submit", "", False),  # a button's default type, out of any form
        ("input", "button", "", False),
        ("input", "submit", "", False),
        ("div", "", "button", False),
        ("span", "", "button", False),
    ],
)
def test_a_decline_button_may_be_any_plain_button(tag, kind, role, in_form):
    shape = button("Tout refuser", tag=tag, kind=kind, role=role)
    shape["in_form"] = shape["point"]["in_form"] = in_form
    assert click(shape) is None


def test_typing_or_choosing_in_a_banner_stays_refused():
    shape = button("No, thanks")
    for tool in ("browser_type", "browser_select_option"):
        assert discovery.verdict(shape, tool, START, scope=SCOPE, args={"text": "x"})


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://www.example-jungle.test/fr/jobs", True),  # the search page itself
        ("https://www.example-jungle.test/en/jobs", True),
        ("https://www.example-jungle.test/jobs", True),
        ("https://www.example-jungle.test/search", True),
        ("https://www.example-jungle.test/fr/jobs?query=genai+engineer&contract=CDI", True),
        ("https://www.example-jungle.test/fr/jobs?query=GenAI%20Engineer&contract=CDI", True),
        ("https://www.example-jungle.test/fr/jobs?query=genai+engineer", False),  # a filter missing
        ("https://www.example-jungle.test/fr/jobs?contract=CDI", False),  # no saved title
        ("https://www.example-jungle.test/fr/jobs?query=sales&contract=CDI", False),
        ("https://www.example-jungle.test/fr/jobs?page=2", False),
        ("https://www.example-jungle.test/fr/jobs?", True),  # an empty query is no query
        ("https://www.example-jungle.test/fr/companies/acme/jobs/genai-engineer", True),
        ("https://www.example-jungle.test/fr/jobs/apply", False),
        ("https://www.example-jungle.test/fr/signup/jobs", False),
        ("https://elsewhere.example.test/fr/jobs", False),
    ],
)
def test_the_search_page_opens_and_a_search_still_carries_a_saved_title(url, allowed):
    assert discovery.read_url(url, START, scope=SCOPE) is allowed


# Paths whose last segment is an endpoint at another depth, probed in the review of PR 720
# (round 2): an account, an admin, a sign in or a consent page is no search page.
DEEP = [
    "/settings/jobs", "/preferences/jobs", "/me/jobs", "/my/jobs", "/dashboard/jobs",
    "/admin/jobs", "/inbox/search", "/notifications/jobs", "/billing/search",
    "/fr/mon-compte/jobs", "/fr/profil/jobs", "/oauth/authorize/jobs", "/sso/jobs", "/auth/jobs",
    "/session/new/search", "/consent/accept/jobs", "/cookies/accept/jobs", "/fr/me/jobs",
    "/xx/jobs", "/FR/jobs", "/fr/JOBS", "/JOBS", "/fr-france/jobs", "/fr-/jobs", "/fr//jobs",
    "/en-gb/jobs/", "/b13/jungle/fr/jobs",
]  # fmt: skip
SEARCH_PAGES = ["/jobs", "/search/", "/careers", "/vacancies", "/fr/jobs", "/en/search",
                "/en-GB/jobs", "/pt_BR/careers", "/de-de/jobs/"]  # fmt: skip


@pytest.mark.parametrize("path", DEEP)
@pytest.mark.parametrize("signed_in", [False, True])
def test_an_endpoint_at_another_depth_is_no_search_page(path, signed_in):
    """browserd and the api read the search page the same way, `/<endpoint>` or
    `/<language>/<endpoint>`, case sensitive (discovery.search_path, finder.search_page)."""
    from app.browser import finder

    url = "https://www.example-jungle.test" + path
    home = "https://www.example-jungle.test/"
    scope = discovery.Scope(home, ("genai engineer",), signed_in=signed_in)
    deep = path not in {"/en-gb/jobs/"}  # a trailing slash aside, that one is a search page
    assert discovery.search_path(urlsplit(url).path) is not deep
    assert finder.search_page(url) is not deep
    if deep and path.rstrip("/").rsplit("/", 1)[-1] in discovery.ENDPOINTS:
        # browserd reads an endpoint there as a search: it carries the saved criteria. Another
        # last segment (`/JOBS`) is any page to it, opened only from a link the api saw.
        assert not discovery.read_url(url, home, scope=scope)
        assert not discovery.signed_request(url, "GET", True, scope)


@pytest.mark.parametrize("path", SEARCH_PAGES)
@pytest.mark.parametrize("signed_in", [False, True])
def test_the_search_page_at_its_root_or_under_a_language_opens(path, signed_in):
    from app.browser import finder

    url = "https://www.example-jungle.test" + path
    home = "https://www.example-jungle.test/"
    scope = discovery.Scope(home, ("genai engineer",), signed_in=signed_in)
    assert discovery.search_path(urlsplit(url).path)
    assert finder.search_page(url)
    assert discovery.read_url(url, home, scope=scope)
    assert finder.search_page(url + "?query=genai+engineer") is False


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (
            'Timeout 5000ms exceeded.\n  - <div id="axeptio_overlay">…</div> intercepts pointer '
            "events\n  - retrying hover action",
            "element_covered",
        ),
        ("Timeout 5000ms exceeded.\n  - waiting for element to be visible", "page_error"),
        ("Element is not attached to the DOM", "page_error"),
    ],
)
def test_a_click_another_element_covers_is_told_as_covered(monkeypatch, message, code):
    """routes.call_tool: Playwright's error says another element takes the pointer."""

    class PlaywrightLikeError(Exception):
        pass

    async def run(tab, name, args):
        raise PlaywrightLikeError(message)

    answer = call_failing(monkeypatch, run)
    assert answer["error"] == code
    if code == "element_covered":
        assert "cookie banner" in answer["message"]
        assert not any(dash in answer["message"] for dash in ("–", "—", " - "))


@pytest.mark.parametrize("message", ["Target closed", "intercepts pointer events"])
def test_a_guard_that_could_not_be_restored_has_its_own_code(monkeypatch, message):
    """Round 2 of PR 720: a RefusalRecoveryError (Tab.settle could not close a tab off the list
    or take the page back) is `guard_failed`, never the catch all's recoverable `page_error`."""

    async def run(tab, name, args):
        error = RuntimeError(message)
        error.add_note(routes.actions.GUARD_FAILED_NOTE)  # as actions.run re-raises it
        raise error

    answer = call_failing(monkeypatch, run)
    assert answer["error"] == "guard_failed"
    assert not any(dash in answer["message"] for dash in ("–", "—", " - "))


def call_failing(monkeypatch, run) -> dict:
    """routes.call_tool's answer to a browser_click whose actions.run is `run`."""

    async def turn(*_):
        return None

    async def passthrough(svc, lease_id, token, work, **_):
        return await work()

    lease = type(
        "Lease",
        (),
        {
            "request": type("R", (), {"automation": True, "purpose": "sweep"})(),
            "gate_hash": "",
            "app_turn": True,
            "wait_reason": None,
            "lease_id": "0" * 36,
        },
    )()
    svc = type("Svc", (), {"manager": type("M", (), {"check_token": lambda *_: lease})()})()
    monkeypatch.setattr(routes.actions, "run", run)
    monkeypatch.setattr(routes, "_turn", turn)
    monkeypatch.setattr(routes, "_tab", lambda *_, **__: object())
    monkeypatch.setattr(routes, "_run", passthrough)
    request = type("Req", (), {"headers": {}, "json": lambda self: {"ref": "e1"}})()
    match = {"name": "browser_click", "lease_id": "0" * 36}
    status, answer = asyncio.run(routes.call_tool(svc, request, match))
    assert status == 200
    return answer


@pytest.mark.parametrize(
    "url",
    ["https://www.example-jungle.test/en/jobs", "https://www.example-jungle.test/fr/search"],
)
def test_a_redirect_hop_to_a_bare_search_page_stays_refused(url):
    """The rule reviewed with the start page's redirect (test_j18_start_redirect): a hop the
    website sends by itself to a search address carries the saved criteria; the agent's own
    navigation to the search page does not need them."""
    home = discovery.Scope("https://www.example-jungle.test/", ("genai engineer",))
    assert discovery.read_url(url, home.start, scope=home)
    assert not discovery.read_url(url, home.start, scope=home, hop=True)
    assert discovery.redirect_target(home.start, url, home) is None
    signed = discovery.Scope(home.start, home.titles, signed_in=True)
    assert discovery.signed_request(url, "GET", True, signed)
    assert not discovery.signed_request(url, "GET", True, signed, hop=True)


def test_the_api_and_browserd_read_the_search_page_from_the_same_lists():
    from app.browser import finder

    assert finder.SEARCH_PAGES == discovery.ENDPOINTS
    assert finder.SEARCH_LANGUAGES == discovery.LANGUAGES
    assert finder.SEARCH_REGION.pattern == discovery.REGION.pattern
