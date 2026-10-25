"""Native discovery is a separate read permission, never an application grant."""

import asyncio
import copy
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import discovery

from app.browser import finder, sources
from tests.browser.test_b5_actions import Page, call, facts, tab_of


def bind_native(tab, filters=()):
    scope = discovery.Scope("http://pages/", "robot", tuple(sorted(filters)))
    tab.state.scope = scope
    tab.state.discovery = True
    tab.state.native_context = tab.browser.browser.contexts[0]
    return {
        "_discovery": "native_get",
        "_discovery_scope": {
            "start": scope.start,
            "query": scope.query,
            "filters": dict(scope.filters),
        },
    }


def search(**changes):
    shape = facts("Search", tag="button", kind="submit")
    shape["discovery"] = {
        "native": True,
        "action": "https://jobs.example.test/search",
        "method": "get",
        "target": "",
        "fields": [{"tag": "input", "type": "search", "name": "q", "value": "robot"}],
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
    answer = call(page, "browser_click", {"ref": "e1", **bind_native(tab)}, tab)
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


@pytest.mark.parametrize(
    "word",
    [
        "apply",
        "application",
        "applications",
        "contact",
        "message",
        "messages",
        "profile",
        "account",
        "login",
        "signin",
        "register",
        "payment",
        "delete",
        "withdraw",
        "subscribe",
        "save",
        "update",
        "send",
    ],
)
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


class BoundTools:
    def __init__(self):
        self.page = Page("http://pages/")
        self.tab = tab_of(self.page)
        self.tab.request.purpose = "sweep"
        bind_native(self.tab, (("remote", "1"), ("contract", "permanent")))
        fields = [
            {"tag": "input", "type": "search", "name": "q", "value": "robot"},
            {"tag": "input", "type": "text", "name": "location", "value": "Paris"},
            {"tag": "select", "type": "select-one", "name": "remote", "value": "1"},
            {"tag": "select", "type": "select-one", "name": "contract", "value": "permanent"},
        ]
        for index, field in enumerate(fields, 1):
            shape = search(action="http://pages/search", fields=fields, name=field["name"])
            shape.update(tag=field["tag"], type=field["type"])
            element = self.page.add(f"e{index}", shape)

            async def select(values, field=field, **kwargs):
                self.page.calls.append(("selected", field["name"], values))
                return values

            element.select_option = select

    async def snapshot(self):
        return {"ok": True, "url": self.page.url, "snapshot": '- heading "Jobs"'}

    async def call(self, name, args):
        from browserd.tools import actions

        return await actions.run(self.tab, name, args)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("browser_type", {"ref": "e2", "text": "robot"}),
        ("browser_select_option", {"ref": "e3", "values": ["permanent"]}),
        ("browser_select_option", {"ref": "e4", "values": ["1"]}),
        ("browser_select_option", {"ref": "e1", "values": ["1"]}),
    ],
)
def test_scope_binds_values_to_observed_control(name, args):
    tools = BoundTools()
    pages = sources.Pages(
        tools,
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.SystemRandom(),
        monotonic=lambda: 0,
    )
    one = finder.FinderSession(
        pages,
        "http://pages/",
        '- heading "Jobs"',
        "e2e-scope",
        interactive=finder.InteractiveScope(
            "http://pages/", "robot", (("remote", "1"), ("contract", "permanent"))
        ),
    )
    _, error = asyncio.run(one.interact(name, args))
    assert error
    assert not any(c[0] in {"fill", "key", "selected"} for c in tools.page.calls)


@pytest.mark.parametrize(
    ("name", "args", "performed"),
    [
        ("browser_type", {"ref": "e1", "text": "robot"}, "fill"),
        ("browser_select_option", {"ref": "e3", "values": ["1"]}, "selected"),
        ("browser_select_option", {"ref": "e4", "values": ["permanent"]}, "selected"),
    ],
)
def test_scope_preserves_supported_query_and_corresponding_filters(name, args, performed):
    tools = BoundTools()
    answer = asyncio.run(
        tools.call(
            name,
            {
                "ref": args["ref"],
                **args,
                **bind_native(tools.tab, (("remote", "1"), ("contract", "permanent"))),
            },
        )
    )
    assert answer["ok"], answer
    assert any(c[0] == performed for c in tools.page.calls)


@pytest.mark.parametrize(
    "fields",
    [
        [{"name": "q", "value": "other"}],
        [{"name": "q", "value": "robot"}, {"name": "q", "value": "other"}],
        [{"name": "q", "value": "robot"}, {"name": "remote", "value": "0"}],
        [{"name": "q", "value": "robot"}, {"name": "contract", "value": "1"}],
    ],
)
def test_successful_controls_cannot_change_effective_scope(fields):
    fields = [{"tag": "input", "type": "hidden", **f} for f in fields]
    scope = discovery.Scope("https://jobs.example.test/", "robot", (("remote", "1"),))
    assert discovery.verdict(search(fields=fields), "browser_click", scope.start, scope=scope)


@pytest.mark.parametrize("name", ["q", "query", "remote", "contract", "page", "other"])
def test_named_submitter_is_unsupported(name):
    assert discovery.verdict(search(name=name), "browser_click", "https://jobs.example.test/")


@pytest.mark.parametrize("key", sorted(discovery.FIELDS))
def test_duplicate_parameters_refuse_even_when_values_agree(key):
    assert not discovery.read_url(
        f"https://jobs.example.test/search?{key}=robot&{key}=robot", "https://jobs.example.test/"
    )


@pytest.mark.parametrize(
    ("query", "allowed"),
    [
        ("q=robot&remote=1", True),
        ("query=robot&remote=1&page=2", True),
        ("q=other&remote=1", False),
        ("q=robot&remote=permanent", False),
        ("q=robot", False),
        ("q=robot&remote=1&contract=permanent", False),
        ("q=robot&query=robot&remote=1", False),
        ("q=robot&remote=1&page=0", False),
        ("q=robot&remote=1&page=2&page=3", False),
        ("q=robot&remote=1&page=٢", False),
    ],
)
def test_outgoing_request_criteria_independently_match_scope(query, allowed):
    scope = discovery.Scope("https://jobs.example.test/", "robot", (("remote", "1"),))
    assert discovery.read_url(scope.start + "search?" + query, scope.start, scope=scope) is allowed


@pytest.mark.parametrize("endpoint", ["remove", "unsubscribe", "deactivate", "checkout", "buy"])
@pytest.mark.parametrize(
    "variant", ["{}", "{}/", "{}/42", "{}.html", "{}-now", "{}Now", "{}s", "ｒｅｍｏｖｅ"]
)
def test_destructive_path_families_refuse_actions_and_requests(endpoint, variant):
    url = "https://jobs.example.test/" + variant.format(endpoint)
    shape = search(href=url)
    shape.update(tag="a", type="", names=["Next page"])
    assert not discovery.read_url(url, "https://jobs.example.test/")
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/search",
        "/jobs",
        "/careers",
        "/vacancies",
        "/jobs/42",
        "/jobs/robot",
        "/jobs/robot-engineer",
        "/jobs/1234",
        "/jobs/remote",
        "/careers/engineer",
        "/vacancies/2",
        "/board/search",
        "/board/jobs",
        "/board/careers",
        "/board/vacancies",
        "/jobs/data-scientist",
        "/jobs/research",
        "/jobs/robotics",
        "/jobs/software-engineer",
        "/jobs/pays",
        "/jobs/paysage",
        # Neighbours of the apply shaped paths (round 3) that are job slugs, not applications.
        "/jobs/applied-scientist",
        "/jobs/applied-ai-engineer",
        "/jobs/supply-chain-analyst",
        "/jobs/candidate-experience-lead",
        "/jobs/solicitor",
        "/jobs/happy-path-engineer",
        "/jobs/bewertung-analyst",
        "/jobs/postdoc-robotics",
        "/careers/candidates",
    ],
)
def test_supported_read_destinations_stay_untouched(path):
    assert discovery.read_url("https://jobs.example.test" + path, "https://jobs.example.test/")


@pytest.mark.parametrize("status", [200, 301, 302, 303, 304, 307, 308])
def test_response_interception_keeps_headers_or_aborts_before_redirect(status):
    tab = tab_of(Page())
    bind_native(tab)
    commands = []

    async def send(name, args):
        commands.append((name, args))

    tab.state.native_cdp = SimpleNamespace(send=send)
    headers = [
        {"name": "Set-Cookie", "value": "synthetic=1"},
        {"name": "Content-Security-Policy", "value": "default-src 'self'"},
    ]
    event = {
        "requestId": "one",
        "request": {"method": "GET", "url": "http://pages/"},
        "responseStatusCode": status,
        "responseHeaders": headers,
    }
    asyncio.run(tab.intercept(event))
    assert len(commands) == 1
    if status == 200:
        assert commands[0][0] == "Fetch.continueResponse"
        result = commands[0][1]["responseHeaders"]
        assert result[:-1] == headers
        assert "script-src 'none'" in result[-1]["value"]
        assert "frame-src 'none'" in result[-1]["value"]
    else:
        assert commands[0] == (
            "Fetch.failRequest",
            {"requestId": "one", "errorReason": "BlockedByClient"},
        )
        assert tab.state.blocked


def test_interception_error_closes_context_and_never_falls_back():
    tab = tab_of(Page())
    bind_native(tab)
    closed = []

    async def send(*args):
        raise RuntimeError("synthetic interception failure")

    async def close():
        closed.append(True)

    tab.state.native_cdp = SimpleNamespace(send=send)
    tab.state.native_context.close = close
    asyncio.run(
        tab.intercept({"requestId": "one", "request": {"method": "GET", "url": "http://pages/"}})
    )
    assert closed == [True]
    assert tab.state.native_failed
    assert call(tab.state.page, "browser_snapshot", {}, tab)["error"] == "unsupported_discovery"


def test_memory_eviction_reaches_discovery_context_and_stops_its_tools():
    from browserd import chromium

    native, persistent = Page(), Page()
    tab = tab_of(native)
    bind_native(tab)
    tab.browser.browser.contexts.insert(0, SimpleNamespace(pages=[persistent]))
    events = []
    assert asyncio.run(
        chromium.guard_memory(
            tab.browser.browser, 90, lambda **e: events.append(e), lambda: (100, 100)
        )
    )
    assert native.closed
    assert not persistent.closed
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 0}]
    assert call(native, "browser_snapshot", {}, tab)["error"] == "unsupported_discovery"


APPLY_SHAPED = [
    "/easyapply",
    "/quickapply",
    "/oneclickapply",
    "/applynow",
    "/reapply",
    "/jobs/42/easyapply",
    "/jobs/42/apply",
    "/jobs/42/candidature",
    "/candidature-simplifiee",
    "/candidatures",
    "/candidater",
    "/candidatez",
    "/jobs/robot/postuler",
    "/postulation",
    "/bewerben",
    "/jetzt-bewerben",
    "/jetztbewerben",
    "/bewerbung",
    "/bewirb-dich",
    "/solliciteer",
    "/sollicitatie",
    "/candidatura",
]


@pytest.mark.parametrize("path", APPLY_SHAPED)
@pytest.mark.parametrize(
    "variant", [str, str.upper, str.title, lambda p: p + "/", lambda p: p + ".html", "{}?page=2"]
)
def test_apply_shaped_paths_refuse_links_and_requests(path, variant):
    path = variant.format(path) if isinstance(variant, str) else variant(path)
    url = "https://jobs.example.test" + path
    shape = search(href=url)
    shape.update(tag="a", type="", names=["Next page"])
    assert not discovery.read_url(url, "https://jobs.example.test/")
    assert discovery.verdict(shape, "browser_click", "https://jobs.example.test/")


@pytest.mark.parametrize(
    "path", ["/ＥａｓｙＡｐｐｌｙ", "/Quick_Apply", "/QuickApply", "/easy%61pply"]
)
def test_apply_shaped_paths_refuse_in_other_spellings(path):
    assert not discovery.read_url("https://jobs.example.test" + path, "https://jobs.example.test/")
