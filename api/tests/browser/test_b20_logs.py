"""B20: what browserd's log says of a website search, and the paths whose tests went with the B13
to B19 files (review of B20, round 1): SPEC-browser's "a refusal of the line is logged with its
code and the element's tag, role and type, or the request's host and kind, never a name, a path
or a query"; a click or a typing whose element never gets ready says why (`not_ready`), and
outside a search a timeout still raises; the route's refusals logged per lease are bounded
(ROUTE_LOGGED); a covered box is typed through the focus in every search, and one that takes no
focus types nothing. Also the sign out refusals of browser_navigate, Back and Forward and a
redirect hop, before anything moves.

Only the Playwright and CDP boundary is fake; every privacy marker is a planted random token.
"""

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import pages
from browserd.tools import actions, discovery

TOKEN = "q" + uuid.uuid4().hex[:12]  # in a name, a path, a query, a page's text
JOBS = "https://jobs.example.test"
SECRET_URL = f"{JOBS}/in/{TOKEN}/jobs?keywords={TOKEN}&geo={TOKEN}"


class TimeoutError(Exception):  # Playwright's TimeoutError is matched by its name
    pass


async def nothing(*_: Any, **__: Any) -> None:
    return None


async def no_dwell(*_: Any, **__: Any) -> None:
    raise AssertionError("refused before the dwell")


def tab(*, search: bool = True, signed_in: bool = False) -> actions.Tab:
    found = actions.Tab(
        request=SimpleNamespace(hosts=(), submit_selectors=(), purpose="sweep"),
        state=actions.State(),
        browser=None,
        pacer=SimpleNamespace(
            dwell=nothing,
            read=nothing,
            hover=nothing,
            sleep=nothing,
            key_delays_s=lambda n: [0] * n,
        ),
        lease_tag="e2e-l1",
        discovery=search,
    )
    found.state.discovery = search
    found.state.signed_in = signed_in
    if search:
        found.state.scope = discovery.Scope(JOBS + "/", signed_in)
    return found


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "browserd"]


def button(name: str, **extra: Any) -> dict[str, Any]:
    facts = {"tag": "button", "type": "button", "role": "", "names": [name], "credential": False}
    return {**facts, **extra}


# --- a refusal of a control: its code, tag, role and type, never a name -------------------------


@pytest.mark.parametrize(
    ("facts", "code", "described"),
    [
        (button(f"Connect with {TOKEN}"), "line", "tag=button role=- type=button"),
        (button(f"Apply {TOKEN}", type="submit"), "submit", "tag=button role=- type=submit"),
        (button(f"Delete {TOKEN}"), "danger", "tag=button role=- type=button"),
        (
            {"tag": "a", "type": "", "role": "menuitem", "names": [f"Sign out {TOKEN}"]},
            "line",
            "tag=a role=menuitem type=-",
        ),
    ],
)
def test_a_refused_control_is_logged_by_its_code_and_shape_never_its_name(
    facts, code, described, caplog
) -> None:
    found = tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        assert found.judge({**facts, "credential": False}, "click", "browser_click") == (
            actions.refused(code)
        )
    assert logged(caplog) == [
        f"lease e2e-l1: browser_click refused by the line: {code} {described}"
    ]


def test_a_control_s_facts_logged_are_cut_to_word_characters() -> None:
    shaped = {"tag": f"div<{TOKEN}>", "role": "x" * 50, "type": None}
    assert actions.described(shaped) == f"tag=div{TOKEN}"[:24] + " role=" + "x" * 20 + " type=-"
    assert actions.described("unreadable facts") == "facts=unreadable"


# --- a refused request: why, its host and its kind, never its path or query, bounded ------------


class Route:
    def __init__(self) -> None:
        self.done: list[str] = []

    async def fallback(self) -> None:
        self.done.append("fallback")

    async def abort(self, code: str = "") -> None:
        self.done.append("abort")

    async def fulfill(self, **_: object) -> None:
        self.done.append("fulfill")


def request(url: str, method: str = "POST", kind: str = "fetch", frame: str | None = None) -> Any:
    """A request: `frame` "main" or "sub" for a document, None for anything else."""
    parent = None if frame == "main" else SimpleNamespace()
    return SimpleNamespace(
        url=url,
        method=method,
        resource_type=kind if frame is None else "document",
        is_navigation_request=lambda: frame is not None,
        frame=SimpleNamespace(parent_frame=parent),
    )


def routed(found: actions.Tab, req: Any) -> list[str]:
    route = Route()
    asyncio.run(found._line_route(route, req))
    return route.done


def test_a_refused_request_is_logged_by_why_host_and_kind_never_its_path_or_query(caplog) -> None:
    found = tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        assert routed(found, request(f"{JOBS}/api/messages/{TOKEN}?to={TOKEN}")) == ["abort"]
        assert routed(found, request(f"{JOBS}/jobs/{TOKEN}?q={TOKEN}", frame="main")) == ["fulfill"]
        assert routed(found, request(f"{JOBS}/m/logout/?u={TOKEN}", "GET", "image")) == ["abort"]
        assert routed(found, request(f"{JOBS}/{TOKEN}?q={TOKEN}", "GET", "image")) == ["fallback"]
    assert logged(caplog) == [
        "lease e2e-l1: a request refused by the line: line_post host=jobs.example.test kind=fetch",
        "lease e2e-l1: a request refused by the line: document_post host=jobs.example.test "
        "kind=document",
        "lease e2e-l1: a request refused by the line: sign_out host=jobs.example.test kind=image",
    ]
    assert found.state.stopped_kind == "document:document_post"  # what settle() logs, no path


def test_a_link_or_a_popup_to_a_sign_out_address_is_a_stopped_navigation() -> None:
    """A main frame GET to a sign out address (a link, `location.href`, a popup's first load) is
    answered 204, so the tab stays where it was, and settle() answers `line`."""
    found = tab()
    url = f"https://www.linkedin.test/m/logout/?u={TOKEN}"
    assert routed(found, request(url, "GET", frame="main")) == ["fulfill"]
    assert found.state.blocked == [url]
    assert found.state.stopped_kind == "document:sign_out"
    assert routed(found, request(f"{JOBS}/jobs/42", "GET", frame="main")) == ["fallback"]


def test_the_route_s_refusals_logged_are_bounded_per_lease(caplog) -> None:
    found = tab()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        for _ in range(actions.ROUTE_LOGGED + 5):
            assert routed(found, request(f"{JOBS}/api/messages")) == ["abort"]
    assert len(logged(caplog)) == actions.ROUTE_LOGGED
    assert found.state.route_logged == actions.ROUTE_LOGGED
    found.state.end()
    assert found.state.route_logged == 0  # the next lease logs again


def test_a_host_logged_is_letters_digits_dots_and_hyphens_or_unreadable() -> None:
    assert actions.host_of(SECRET_URL) == "jobs.example.test"
    assert actions.host_of(f"https://{TOKEN}:{TOKEN}@jobs.example.test/x") == "jobs.example.test"
    assert actions.host_of("https://jobs_example.test/") == "unreadable"
    assert actions.host_of(7) == "unreadable"


# --- browser_navigate: a failure logs its host, a sign out never opens --------------------------


class Page:
    url = JOBS + "/"

    def is_closed(self) -> bool:
        return False


def navigated(found: actions.Tab, url: str, answer: dict, monkeypatch) -> dict:
    page = Page()

    async def front() -> Page:
        return page

    async def settle(result: dict) -> dict:
        return result

    async def navigate(at: Any, address: str, **_: Any) -> dict:
        assert address == url
        return answer

    found.page, found.settle = front, settle
    monkeypatch.setattr(pages, "navigate", navigate)
    return asyncio.run(actions.navigate(found, {"url": url}))


def test_a_page_that_did_not_load_logs_why_and_its_host_never_its_path_or_query(
    monkeypatch, caplog
) -> None:
    failed = {
        "ok": False,
        "error": "navigation_failed",
        "detail": f"net::ERR_NAME_NOT_RESOLVED at {SECRET_URL} {TOKEN}",
    }
    with caplog.at_level(logging.WARNING, logger="browserd"):
        assert navigated(tab(), SECRET_URL, failed, monkeypatch) == failed
    assert logged(caplog) == [
        "lease e2e-l1: browser_navigate failed: navigation_failed "
        "net=net::ERR_NAME_NOT_RESOLVED host=jobs.example.test"
    ]


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.linkedin.test/m/logout/?u={TOKEN}",
        f"https://secure.indeed.test/auth/logout?continue={TOKEN}",
        f"{JOBS}/de/abmelden",
    ],
    ids=["linkedin", "indeed", "german"],  # a planted token never names a test (xdist)
)
def test_a_typed_navigation_to_a_sign_out_address_is_refused_before_the_dwell(
    url, monkeypatch, caplog
) -> None:
    found = tab()
    found.pacer.dwell = no_dwell
    with caplog.at_level(logging.WARNING, logger="browserd"):
        assert navigated(found, url, {"ok": True}, monkeypatch) == actions.refused("line")
    [line] = logged(caplog)
    assert line.startswith("lease e2e-l1: browser_navigate refused by the line: sign_out host=")
    assert TOKEN not in line
    assert "logout" not in line


def test_outside_a_search_a_sign_out_address_is_the_lease_s_own_rule(monkeypatch) -> None:
    found = tab(search=False)
    found.request.hosts = ("jobs.example.test",)
    assert navigated(found, f"{JOBS}/logout", {"ok": True}, monkeypatch) == {"ok": True}


class History:
    """A tab's history as CDP answers it."""

    def __init__(self, urls: list[str], current: int) -> None:
        self.urls, self.current = urls, current

    async def new_cdp_session(self, page: Any) -> Any:
        async def send(method: str, params: Any = None) -> dict:
            assert method == "Page.getNavigationHistory"
            return {"currentIndex": self.current, "entries": [{"url": u} for u in self.urls]}

        return SimpleNamespace(send=send, detach=nothing)


@pytest.mark.parametrize(
    ("urls", "current", "back"),
    [
        ([f"{JOBS}/m/logout/", f"{JOBS}/jobs"], 1, True),
        ([f"{JOBS}/jobs", f"{JOBS}/sign-out?u={TOKEN}"], 0, False),
    ],
)
def test_back_or_forward_to_a_sign_out_entry_is_refused_before_it_moves(
    urls, current, back, caplog
) -> None:
    found = tab()
    found.pacer.dwell = no_dwell
    page = SimpleNamespace(context=History(urls, current))

    async def front() -> Any:
        return page

    found.page = front
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = asyncio.run(actions.navigate_history(found, {}, back=back))
    assert result == actions.refused("line")
    [line] = logged(caplog)
    assert line.endswith("refused by the line: sign_out")


def test_back_with_no_entry_before_is_no_history() -> None:
    found = tab()
    found.pacer.dwell = no_dwell
    page = SimpleNamespace(context=History([f"{JOBS}/jobs"], 0))

    async def front() -> Any:
        return page

    found.page = front
    result = asyncio.run(actions.navigate_history(found, {}, back=True))
    assert result["error"] == "no_history"


def test_a_redirect_hop_to_a_sign_out_address_is_stopped_and_answered_line() -> None:
    found = tab()
    stopped: list[str] = []
    frame = SimpleNamespace(parent_frame=None)

    async def evaluate(script: str) -> None:
        stopped.append(script)

    frame.evaluate = evaluate

    def hop(url: str) -> Any:
        return SimpleNamespace(
            url=url, redirected_from=object(), is_navigation_request=lambda: True, frame=frame
        )

    async def run() -> None:
        found._hop(hop(f"{JOBS}/jobs/42"))  # any public address goes on
        assert found.state.blocked == []
        found._hop(hop(f"{JOBS}/uas/logout?u={TOKEN}"))
        await asyncio.sleep(0)

    asyncio.run(run())
    assert found.state.blocked == [f"{JOBS}/uas/logout?u={TOKEN}"]
    assert found.state.stopped_kind == "redirect"
    assert stopped == ["() => window.stop()"]


# --- an element that never gets ready: why, in the code's words, never the page's ----------------


class Located:
    pass


def not_ready(message: str, point: Any, *, search: bool = True) -> dict:
    found = tab(search=search)

    async def element(args: dict) -> tuple[Any, Any]:
        return Located(), None

    async def approach(locator: Any) -> None:
        raise TimeoutError(message)

    async def facts(locator: Any, point: bool = False) -> dict:
        return {**button("Robot Engineer"), "point": shown}

    shown = point
    found.element, found.approach, found.facts = element, approach, facts
    return asyncio.run(actions.click(found, {"ref": "e7"}))


def test_a_click_behind_a_layer_says_so_and_never_the_page_s_text(caplog) -> None:
    overlay = {**button("x", tag="div", type="", role="dialog"), "inside": False}
    message = (
        f'Timeout 5000ms exceeded. <div class="{TOKEN}">Jane {TOKEN}</div> intercepts pointer'
        " events"
    )
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready(message, overlay)
    assert result["error"] == "element_covered"
    assert logged(caplog) == [
        "lease e2e-l1: browser_click not ready: reason=covered covered_by=div/dialog"
    ]


def test_a_transparent_layer_is_told_by_where_the_click_lands(caplog) -> None:
    layer = {**button("", tag="div", type=""), "inside": False}
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready("Timeout 5000ms exceeded. element is not stable", layer)
    assert result["error"] == "element_covered"
    assert logged(caplog)[0].endswith("reason=moving covered_by=div/-")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Timeout 5000ms exceeded. element is not visible", "not_visible"),
        ("Timeout 5000ms exceeded. element is outside of the viewport", "outside_viewport"),
        ("Timeout 5000ms exceeded. element is not enabled", "disabled"),
        (f"Timeout 5000ms exceeded. {TOKEN}", "unknown"),
    ],
    ids=["not_visible", "outside_viewport", "disabled", "unknown"],
)
def test_an_element_that_never_gets_ready_says_why(message, reason, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = not_ready(message, None)
    assert result == actions.failure("page_error", actions.NOT_READY)
    assert logged(caplog) == [
        f"lease e2e-l1: browser_click not ready: reason={reason} covered_by=-"
    ]


def test_outside_a_search_a_click_timeout_still_raises() -> None:
    """An apply lease's click that times out is no `not_ready` answer: it raises (focus 2)."""
    with pytest.raises(TimeoutError):
        not_ready("element is not visible", None, search=False)


# --- a covered box is typed through the focus, in every search ----------------------------------


class Box:
    """A box another element covers: the hover times out, focus and fill work."""

    def __init__(self, focused: bool = True) -> None:
        self.focused, self.calls = focused, []

    async def focus(self, **_: Any) -> None:
        self.calls.append("focus")

    async def evaluate(self, script: str, arg: Any = None, **_: Any) -> Any:
        assert script == actions.FOCUSED_JS
        return self.focused

    async def click(self, **_: Any) -> None:
        self.calls.append("click")

    async def fill(self, value: str, **_: Any) -> None:
        self.calls.append(f"fill:{value}")


BOX = {
    "tag": "input",
    "type": "text",
    "role": "combobox",
    "names": [f"Where {TOKEN}"],
    "credential": False,
    "in_form": False,
}


def typed(box: Box, *, signed_in: bool, message: str = "x intercepts pointer events"):
    found = tab(signed_in=signed_in)
    keys: list[str] = []

    async def record(value: str) -> None:
        keys.append(value)

    async def press(key: str) -> None:
        keys.append(f"<{key}>")

    page = SimpleNamespace(keyboard=SimpleNamespace(type=record, insert_text=record, press=press))

    async def element(args: dict) -> tuple[Any, Any]:
        return box, None

    async def approach(locator: Any) -> None:
        raise TimeoutError(f"Timeout 5000ms exceeded. {message}")

    asked: list[bool] = []

    async def facts(locator: Any, point: bool = False) -> dict:
        asked.append(point)
        return dict(BOX)

    async def front() -> Any:
        return page

    async def settle(result: dict) -> dict:
        return result

    async def here(_: Any) -> dict:
        return {"ok": True, "url": JOBS + "/jobs"}

    found.element, found.approach, found.facts = element, approach, facts
    found.page, found.settle = front, settle
    original, actions._here = actions._here, here
    try:
        result = asyncio.run(
            actions.type_text(found, {"ref": "e9", "text": "Paris", "submit": True})
        )
    finally:
        actions._here = original
    return result, keys, asked


@pytest.mark.parametrize("signed_in", [False, True], ids=["anonymous", "signed_in"])
def test_a_covered_box_is_focused_and_typed_in(signed_in, caplog) -> None:
    box = Box()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result, keys, asked = typed(box, signed_in=signed_in)
    assert result["ok"], result
    assert box.calls == ["focus", "fill:"]  # never a click on what covers it
    assert "".join(k for k in keys if not k.startswith("<")) == "Paris"
    assert keys[-1] == "<Enter>"
    assert asked == [False]  # judged on its own facts: no click lands anywhere
    assert logged(caplog) == ["lease e2e-l1: browser_type covered: typed through the focus"]


@pytest.mark.parametrize("signed_in", [False, True], ids=["anonymous", "signed_in"])
def test_a_covered_box_that_takes_no_focus_types_nothing(signed_in) -> None:
    box = Box(focused=False)
    result, keys, _ = typed(box, signed_in=signed_in)
    assert result == actions.failure("element_covered", actions.BOX_COVERED)
    assert keys == []
    assert "click" not in box.calls


def test_a_box_that_is_not_covered_but_never_ready_says_why(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result, keys, _ = typed(Box(), signed_in=False, message="element is not visible")
    assert result == actions.failure("page_error", actions.NOT_READY)
    assert keys == []
    assert logged(caplog) == [
        "lease e2e-l1: browser_type not ready: reason=not_visible covered_by=-"
    ]
