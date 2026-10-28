"""A cookie banner is closed by declining it, and only that (2026-10-28, issue 716): Welcome to
the Jungle's "C is for COOKIE" banner covered its search page, and browserd refused every click
of a discovery that was no native search, so the agent could not close it. browserd now takes
one more click in a discovery: a button whose name is a decline phrase (DECLINES), no other name
of it accepting, allowing or opening settings, never a link nor a form's submit button, at its
click point too. Accepting stays refused. The search page itself, its address with no query
(`/fr/jobs`), opens; a search request still carries a saved title. A click Playwright could not
make because another element covers it is told as `element_covered`.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

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
    assert (status, answer["error"]) == (200, code)
    if code == "element_covered":
        assert "cookie banner" in answer["message"]
        assert not any(dash in answer["message"] for dash in ("–", "—", " - "))


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
