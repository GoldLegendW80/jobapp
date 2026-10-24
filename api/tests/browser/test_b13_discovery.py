"""Native discovery is a separate read permission, never an application grant."""

import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import discovery

from tests.browser.test_b5_actions import Page, call, facts, tab_of


def search(**changes):
    shape = facts("Search", tag="button", kind="submit")
    shape["discovery"] = {
        "native": True,
        "action": "https://jobs.example.test/search",
        "method": "get",
        "target": "",
        "fields": [{"tag": "input", "type": "search", "name": "q"}],
        "name": "",
        "href": "",
    }
    shape["discovery"].update(changes)
    return shape


@pytest.mark.parametrize("endpoint", ["search", "jobs", "careers", "vacancies"])
@pytest.mark.parametrize("query", ["q", "query", "keywords", "keyword", "search"])
def test_native_get_forms_allow_search_without_gate(endpoint, query):
    shape = search(
        action=f"https://jobs.example.test/board/{endpoint}",
        fields=[{"tag": "input", "type": "search", "name": query}],
    )
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "post"},
        {"method": "dialog"},
        {"method": "GET "},
        {"action": "https://other.example.test/search"},
        {"action": "http://jobs.example.test/search"},
        {"action": "https://user@jobs.example.test/search"},
        {"action": "javascript:search()"},
        {"action": "https://jobs.example.test/apply"},
        {"action": "https://jobs.example.test/search/apply"},
        {"action": "https://jobs.example.test/profile/search"},
        {"action": "https://jobs.example.test/contact/search"},
        {"action": "https://jobs.example.test/search?send=1"},
        {"target": "_blank"},
        {"target": "other"},
        {"native": False},
        {"native": None},
        {"fields": []},
        {"fields": [{"tag": "input", "type": "password", "name": "q"}]},
        {"fields": [{"tag": "input", "type": "email", "name": "email"}]},
        {"fields": [{"tag": "input", "type": "hidden", "name": "apply"}]},
        {"fields": [{"tag": "textarea", "type": "textarea", "name": "q"}]},
        {"fields": [{"tag": "input", "type": "file", "name": "q"}]},
        {"fields": [{"tag": "input", "type": "search", "name": "query[apply]"}]},
        {"fields": [{"tag": "input", "type": "search", "name": "Q"}]},
        {"fields": [{"tag": "input", "type": "search", "name": "q" * 501}]},
        {"fields": [{"tag": "input", "type": "search", "name": "q"}] * 101},
        {"method": ""},
        {"action": ""},
        {"action": "https://jobs.example.test/search#apply"},
        {"action": "https://jobs.example.test/%61pply/search"},
    ],
)
def test_search_labels_do_not_authorize_unknown_or_write_forms(changes):
    assert discovery.verdict(search(**changes), "browser_click", "https://jobs.example.test/")


@pytest.mark.parametrize(
    "name",
    [
        "Apply",
        "Send message",
        "Save profile",
        "Delete",
        "Pay",
        "Submit application",
        "Postuler",
        "Envoyer",
        "Mettre à jour",
        "Confirm application",
    ],
)
def test_existing_danger_application_and_profile_names_still_refuse(name):
    shape = search()
    shape["names"] = [name]
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/")


def test_point_and_field_shape_are_checked_and_enter_is_never_granted():
    shape = search()
    shape["point"] = copy.deepcopy(shape)
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/") is None
    shape["point"]["discovery"]["method"] = "post"
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/")
    field = search(name="q")
    field.update(tag="input", type="search")
    assert discovery.verdict(field, "browser_type", "https://jobs.example.test/") is None
    field["discovery"]["name"] = "password"
    assert discovery.verdict(field, "browser_type", "https://jobs.example.test/")
    assert discovery.verdict(search(), "browser_press_key", "https://jobs.example.test/")


def test_discovery_search_submit_succeeds_while_legacy_submit_still_refuses():
    page = Page()
    shape = search(action="http://pages/search")
    page.add("e1", shape)
    tab = tab_of(page)
    tab.request.purpose = "sweep"
    assert call(page, "browser_click", {"ref": "e1"}, tab)["error"] == "submit"
    answer = call(page, "browser_click", {"ref": "e1", "_discovery": "native_get"}, tab)
    assert answer["ok"], answer
    assert ("click",) in page.calls
    assert not tab.gate


@pytest.mark.parametrize("purpose", ["apply_fill", "apply_submit", "profile_edit", "apply_manual"])
def test_discovery_marker_on_other_purposes_cannot_grant_any_action(purpose):
    page = Page()
    page.add("e1", search(action="http://pages/search"))
    tab = tab_of(page)
    tab.request.purpose = purpose
    assert not call(page, "browser_click", {"ref": "e1", "_discovery": "native_get"}, tab)["ok"]
    assert ("click",) not in page.calls


@pytest.mark.parametrize("word", sorted(discovery.WRITES))
def test_write_endpoints_cannot_hide_under_native_search(word):
    assert discovery.verdict(
        search(action=f"https://jobs.example.test/{word}/search"),
        "browser_click",
        "https://jobs.example.test/",
    )


@pytest.mark.parametrize(
    ("method", "url", "blocked"),
    [
        ("GET", "http://pages/search?q=robot", False),
        ("HEAD", "http://pages/jobs", False),
        ("POST", "http://pages/search", True),
        ("PUT", "http://pages/search", True),
        ("PATCH", "http://pages/search", True),
        ("DELETE", "http://pages/search", True),
        ("GET", "http://pages/delete", True),
        ("GET", "http://pages/search?send=1", True),
        ("GET", "http://elsewhere/search", True),
    ],
)
def test_discovery_request_policy_covers_subresources_and_writes(method, url, blocked):
    page = Page()
    tab = tab_of(page)
    tab.state.discovery = True
    calls = []

    async def abort(*args):
        calls.append("abort")

    async def fallback():
        calls.append("fallback")

    request = SimpleNamespace(method=method, url=url, is_navigation_request=lambda: False)
    asyncio.run(tab._guard(SimpleNamespace(abort=abort, fallback=fallback), request))
    assert calls == ["abort" if blocked else "fallback"]
