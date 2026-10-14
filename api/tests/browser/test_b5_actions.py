"""The tools of browserd/tools/actions.py on a fake page, without docker (browser B5): what each
tool does to the page and in which order, given the facts FACTS_JS would answer. The reviewer's
probes of PR #297 are kept here as tests: a typing aimed at a submit button, control characters
typed as keys, Enter with the focus in a frame or a shadow root, a click on a wrapper whose centre
is the submit, and a page that left the allow list. The same tools on a real Chromium are
test_b5_container.py.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import detect, pages  # noqa: E402
from browserd.tools import actions, pacing  # noqa: E402

ON = "http://pages/b5/form.html"
OFF = "http://mail.example/inbox"


def facts(*names: str, tag: str = "input", kind: str = "text", **more: Any) -> dict[str, Any]:
    """What FACTS_JS answers for an element (no click point)."""
    return {
        "tag": tag,
        "type": kind,
        "role": "",
        "in_form": True,
        "names": list(names),
        "selector_hit": False,
        "selector_error": False,
        "holds_submit": False,
        "custom_submit": False,
        "editable": False,
        "field": "",
        "form_fields": [],
        **more,
    }


EMAIL = facts("Email", kind="email", field="email")
TEXTAREA = facts("Why us?", tag="textarea", kind="textarea")
SUBMIT = facts("Submit application", tag="button", kind="submit")


class Element:
    """A locator or an element handle: answers FACTS_JS with `facts`, its click point being
    `point` (the element itself by default), and records what is done to it."""

    def __init__(
        self, page: "Page", facts: dict[str, Any], point: Any = "self", tag: str = "INPUT",
        frame: Any = None,
    ) -> None:  # fmt: skip
        self.page, self.f, self.point, self.tag, self.frame = page, facts, point, tag, frame

    async def count(self) -> int:
        return 1

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if js == detect.FACTS_JS:
            assert set(arg) == {"sels", "point"}
            self.page.calls.append(("facts", arg["point"]))
            answer = dict(self.f)
            if arg["point"]:
                answer["point"] = {**self.f, "inside": True} if self.point == "self" else self.point
            return answer
        if js == actions.FOCUS_TAG_JS:
            return self.tag
        raise AssertionError(js)

    async def content_frame(self) -> Any:
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame

    async def scroll_into_view_if_needed(self, **kw: Any) -> None:
        self.page.calls.append(("scroll",))

    async def hover(self, **kw: Any) -> None:
        pass

    async def click(self, **kw: Any) -> None:
        self.page.calls.append(("click",))

    async def fill(self, value: str, **kw: Any) -> None:
        self.page.calls.append(("fill", value))

    async def set_checked(self, checked: bool, **kw: Any) -> None:
        self.page.calls.append(("set_checked", checked))

    async def screenshot(self, **kw: Any) -> bytes:
        return b"png of the element"


class Frame:
    """A frame whose focused element (DEEP_FOCUS_JS) is `focused`."""

    def __init__(self, focused: Any = None) -> None:
        self.focused = focused

    async def evaluate_handle(self, js: str) -> Any:
        assert js == actions.DEEP_FOCUS_JS
        if isinstance(self.focused, Exception):
            raise self.focused
        return self.focused


class Keyboard:
    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.calls = calls

    async def type(self, char: str) -> None:
        self.calls.append(("key", char))

    async def insert_text(self, text: str) -> None:
        self.calls.append(("insert", text))

    async def press(self, key: str) -> None:
        self.calls.append(("press", key))


class MainFrame:
    def __init__(self, page: "Page") -> None:
        self.page, self.parent_frame = page, None

    @property
    def url(self) -> str:
        return self.page.frame_url or self.page.url

    async def evaluate(self, js: str) -> None:
        self.page.calls.append(("stop",))


class Page(Frame):
    def __init__(self, url: str = ON) -> None:
        super().__init__()
        self.url, self.frame_url, self.history = url, "", [ON]
        self.calls: list[tuple[Any, ...]] = []
        self.keyboard = Keyboard(self.calls)
        self.main_frame = MainFrame(self)
        self.elements: dict[str, Element] = {}
        self.closed = False

    def add(self, ref: str, facts: dict[str, Any], **kw: Any) -> Element:
        self.elements[f"aria-ref={ref}"] = Element(self, facts, **kw)
        return self.elements[f"aria-ref={ref}"]

    def locator(self, selector: str) -> Element:
        return self.elements[selector]

    def is_closed(self) -> bool:
        return self.closed

    async def bring_to_front(self) -> None:
        pass

    async def title(self) -> str:
        return "a title"

    async def screenshot(self, **kw: Any) -> bytes:
        self.calls.append(("screenshot",))
        return b"png"

    async def go_back(self, **kw: Any) -> None:
        self.url, self.frame_url = self.history[-1], ""

    async def goto(self, url: str, **kw: Any) -> None:
        self.url = url

    async def close(self) -> None:
        self.closed = True


class Context:
    def __init__(self, page: Page) -> None:
        self.pages = [page]
        self.listeners: dict[str, Any] = {}

    async def route(self, pattern: str, handler: Any) -> None:
        pass

    def on(self, event: str, handler: Any) -> None:
        self.listeners[event] = handler


class Browser:
    def __init__(self, context: Context) -> None:
        self.browser = self
        self.contexts = [context]


class Request:
    hosts = ("pages",)
    submit_selectors = ("#go",)
    purpose = "apply_fill"


async def no_sleep(seconds: float) -> None:
    pass


def tab_of(page: Page) -> actions.Tab:
    context = Context(page)
    return actions.Tab(
        request=Request(),
        state=actions.State(page=page),
        browser=Browser(context),
        pacer=pacing.Pacer(no_sleep),
    )


def call(page: Page, name: str, args: dict[str, Any], tab: actions.Tab | None = None) -> Any:
    return asyncio.run(actions.run(tab or tab_of(page), name, args))


def acted(page: Page) -> list[tuple[Any, ...]]:
    """What reached the page beyond reading facts and scrolling: clicks, fills, keys."""
    return [c for c in page.calls if c[0] not in ("facts", "scroll")]


# --- 1. browser_type types into a text entry only, judged at its click point -------------------

WRAPPED_SUBMIT = facts("Submit application", tag="button", kind="submit")
NOT_TEXT = [
    pytest.param(SUBMIT, id="submit button"),
    pytest.param(facts("Go", tag="input", kind="image"), id="image input"),
    pytest.param(facts("Submit", tag="input", kind="submit"), id="submit input"),
    pytest.param(facts("Delete my account", tag="button", kind="button"), id="delete button"),
    pytest.param(WRAPPED_SUBMIT, id="label wrapping a submit (its control)"),
    pytest.param(facts("Privacy policy", tag="a", kind=""), id="plain link"),
    pytest.param(facts("Next", tag="button", kind="button"), id="plain button"),
    pytest.param(facts("Remote", tag="input", kind="checkbox"), id="checkbox"),
    pytest.param(facts("Yes", tag="input", kind="radio"), id="radio"),
    pytest.param(facts("CV", tag="input", kind="file"), id="file input"),
    pytest.param(facts("Reset", tag="input", kind="reset"), id="reset input"),
    pytest.param(facts("", tag="input", kind="hidden"), id="hidden input"),
    pytest.param(facts("Volume", tag="input", kind="range"), id="range input"),
    pytest.param(facts("Pays", tag="select", kind="select-one"), id="select"),
    pytest.param(facts("Apply", tag="div", kind=""), id="plain div"),
    pytest.param(facts("Form", tag="form", kind="", editable=True), id="editable form"),
    pytest.param(facts("Go", tag="sl-button", kind="submit", custom_submit=True), id="sl-button"),
    pytest.param(facts("x", tag="button", kind="button", role="textbox"), id="button as textbox"),
]


@pytest.mark.parametrize("target", NOT_TEXT)
def test_typing_into_anything_but_a_text_entry_is_refused_before_a_click(
    target: dict[str, Any],
) -> None:
    page = Page()
    page.add("e5", target)
    result = call(page, "browser_type", {"ref": "e5", "text": "a"})
    assert result["ok"] is False
    assert result["error"] in ("not_editable", "submit", "danger")
    assert acted(page) == []


TEXT_ENTRIES = [
    pytest.param(EMAIL, id="email"),
    pytest.param(facts("Name"), id="text"),
    pytest.param(facts("Name", kind=""), id="input with no type"),
    pytest.param(facts("Phone", kind="tel"), id="tel"),
    pytest.param(facts("Site", kind="url"), id="url"),
    pytest.param(facts("Start", kind="date"), id="date"),
    pytest.param(facts("Years", kind="number"), id="number"),
    pytest.param(TEXTAREA, id="textarea"),
    pytest.param(facts("Letter", tag="div", kind="", editable=True), id="contenteditable"),
    pytest.param(facts("Letter", tag="trix-editor", kind="", editable=True), id="custom editor"),
    pytest.param(facts("City", tag="div", kind="", role="combobox"), id="combobox"),
    pytest.param(facts("Search", tag="div", kind="", role="searchbox"), id="searchbox"),
    pytest.param(facts("Apply", kind="text", field="apply"), id="text field named apply"),
]


@pytest.mark.parametrize("target", TEXT_ENTRIES)
def test_a_text_entry_is_clicked_filled_and_typed(target: dict[str, Any]) -> None:
    page = Page()
    page.add("e5", target)
    result = call(page, "browser_type", {"ref": "e5", "text": "ab"})
    assert result["ok"] is True, result
    assert acted(page) == [("click",), ("fill", ""), ("key", "a"), ("key", "b")]
    # The facts are read after the approach, with the click point.
    assert page.calls[:2] == [("scroll",), ("facts", True)]


def test_a_field_covered_by_a_submit_at_its_click_point_is_refused() -> None:
    page = Page()
    page.add("e5", EMAIL, point={**SUBMIT, "inside": False})
    result = call(page, "browser_type", {"ref": "e5", "text": "a"})
    assert result["error"] == "submit"
    assert acted(page) == []
    # An unreadable point (nothing there) refuses too.
    page = Page()
    page.add("e5", EMAIL, point=None)
    assert call(page, "browser_type", {"ref": "e5", "text": "a"})["error"] == "unreadable"
    assert acted(page) == []


def test_a_label_over_the_field_at_its_click_point_lets_it_type() -> None:
    page = Page()
    label = facts("Email", kind="email", field="email")  # a label's control is the field
    page.add("e5", EMAIL, point={**label, "inside": False})
    assert call(page, "browser_type", {"ref": "e5", "text": "a"})["ok"] is True


def test_the_submit_flag_of_browser_type_is_enter_and_judged_as_one() -> None:
    page = Page()
    page.add("e5", EMAIL)
    result = call(page, "browser_type", {"ref": "e5", "text": "a", "submit": True})
    assert result["error"] == "submit"
    assert acted(page) == []


# --- 2. control characters are never typed as keys ---------------------------------------------

CONTROLS = ["\n", "\r", "\t", "\r\n", "\x0b", "\x0c", "\x00", "\x1b", "\x7f", "\x85", "\x9f",
            "a\n", "a\t\t ", "a\rb", "\tSubmit"]  # fmt: skip


@pytest.mark.parametrize("text", CONTROLS)
def test_control_characters_into_a_one_line_field_are_refused(text: str) -> None:
    page = Page()
    page.add("e5", EMAIL)
    result = call(page, "browser_type", {"ref": "e5", "text": text})
    assert result["ok"] is False
    assert result["error"] == "bad_args"
    assert acted(page) == []


@pytest.mark.parametrize("text", ["\r", "\t", "\r\n", "\x0b", "a\tb"])
def test_control_characters_other_than_a_newline_are_refused_in_a_textarea(text: str) -> None:
    page = Page()
    page.add("e5", TEXTAREA)
    assert call(page, "browser_type", {"ref": "e5", "text": text})["error"] == "bad_args"
    assert acted(page) == []


@pytest.mark.parametrize("target", [TEXTAREA, facts("Letter", tag="div", kind="", editable=True)])
def test_a_newline_into_a_multi_line_field_is_inserted_not_pressed(target: dict[str, Any]) -> None:
    page = Page()
    page.add("e5", target)
    result = call(page, "browser_type", {"ref": "e5", "text": "a\nb c"})
    assert result["ok"] is True, result
    assert acted(page) == [
        ("click",), ("fill", ""), ("key", "a"), ("insert", "\n"), ("key", "b"),
        ("insert", " "), ("key", "c"),
    ]  # fmt: skip
    assert not any(c[0] == "press" for c in page.calls)


def test_a_space_is_inserted_so_a_moved_focus_never_presses_a_button() -> None:
    page = Page()
    page.add("e5", EMAIL)
    assert call(page, "browser_type", {"ref": "e5", "text": "a b"})["ok"] is True
    assert ("key", " ") not in page.calls
    assert ("insert", " ") in page.calls


def test_a_long_text_is_pasted_whole_and_still_checked() -> None:
    page = Page()
    page.add("e5", TEXTAREA)
    text = "x" * pacing.PASTE_FROM + "\nend"
    assert call(page, "browser_type", {"ref": "e5", "text": text})["ok"] is True
    assert acted(page)[-1] == ("insert", text)
    page = Page()
    page.add("e5", EMAIL)
    long_tab = "x" * pacing.PASTE_FROM + "\t"
    assert call(page, "browser_type", {"ref": "e5", "text": long_tab})["error"] == "bad_args"


# --- 3. Enter and Space follow the focus into frames and shadow roots --------------------------


def press(page: Page, key: str) -> Any:
    return call(page, "browser_press_key", {"key": key})


def test_enter_with_the_focus_in_a_field_inside_an_iframe_is_refused() -> None:
    page = Page()
    inner = Element(page, EMAIL, tag="INPUT")
    page.focused = Element(page, facts("", tag="iframe", kind="", in_form=False), tag="IFRAME",
                           frame=Frame(inner))  # fmt: skip
    assert press(page, "Enter")["error"] == "submit"
    assert acted(page) == []
    # Two frames deep: the same.
    middle = Element(page, facts("", tag="iframe", kind=""), tag="IFRAME", frame=Frame(inner))
    page.focused = Element(page, facts("", tag="iframe", kind=""), tag="IFRAME",
                           frame=Frame(middle))  # fmt: skip
    assert press(page, "Enter")["error"] == "submit"


def test_space_with_the_focus_on_a_submit_in_a_shadow_root_is_refused() -> None:
    # DEEP_FOCUS_JS answers the element inside the shadow root, not its host.
    page = Page()
    page.focused = Element(page, SUBMIT, tag="BUTTON")
    assert press(page, "Space")["error"] == "submit"
    page.focused = Element(page, facts("Go", tag="button", kind="button", custom_submit=True),
                           tag="BUTTON")  # fmt: skip
    assert press(page, "Enter")["error"] == "submit"  # the inner button of <sl-button>
    assert acted(page) == []


@pytest.mark.parametrize(
    "frame",
    [None, RuntimeError("cross origin, detached")],
    ids=["no content frame", "frame out of reach"],
)
def test_a_focus_chain_that_cannot_be_followed_refuses_enter_and_space(frame: Any) -> None:
    for key in ("Enter", "Space"):
        page = Page()
        page.focused = Element(page, facts("", tag="iframe", kind=""), tag="IFRAME", frame=frame)
        assert press(page, key)["error"] == "unreadable"
        assert acted(page) == []
    page = Page()
    page.focused = RuntimeError("the page navigated")
    assert press(page, "Enter")["error"] == "unreadable"


def test_frames_nested_past_the_depth_are_refused() -> None:
    page = Page()
    focused: Any = Element(page, EMAIL)
    for _ in range(actions.FRAME_DEPTH + 1):
        focused = Element(page, facts("", tag="iframe", kind=""), tag="IFRAME",
                          frame=Frame(focused))  # fmt: skip
    page.focused = focused
    assert press(page, "Enter")["error"] == "unreadable"


def test_enter_on_the_body_of_a_frame_and_tab_anywhere_go_through() -> None:
    page = Page()
    body = Element(page, facts("", tag="body", kind=""), tag="BODY")
    page.focused = Element(page, facts("", tag="iframe", kind=""), tag="IFRAME", frame=Frame(body))
    assert press(page, "Enter")["ok"] is True
    assert acted(page) == [("press", "Enter")]
    page = Page()
    page.focused = Element(page, SUBMIT, tag="BUTTON")
    assert press(page, "Tab")["ok"] is True
    page = Page()
    page.focused = Element(page, TEXTAREA, tag="TEXTAREA")
    assert press(page, "Enter")["ok"] is True


# --- 4. a click lands where the click point is -------------------------------------------------

CONTAINERS = [
    pytest.param(facts("Continue", tag="div", kind=""), id="wrapper div"),
    pytest.param(facts("Application", tag="form", kind=""), id="form"),
    pytest.param(facts("Your details", tag="fieldset", kind=""), id="fieldset"),
    pytest.param(facts("", tag="section", kind="", in_form=False), id="section"),
]


@pytest.mark.parametrize("wrapper", CONTAINERS)
def test_a_click_on_a_container_whose_centre_is_the_submit_is_refused(
    wrapper: dict[str, Any],
) -> None:
    for tool in ("browser_click", "browser_check"):
        page = Page()
        page.add("e5", wrapper, point={**SUBMIT, "inside": True})
        assert call(page, tool, {"ref": "e5"})["error"] == "submit"
        assert acted(page) == []
    # Held submit with the centre elsewhere: still refused, the wrapper holds a submit.
    page = Page()
    page.add("e5", {**wrapper, "holds_submit": True})
    assert call(page, "browser_click", {"ref": "e5"})["error"] == "submit"


def test_a_click_whose_point_is_unreadable_is_refused() -> None:
    page = Page()
    page.add("e5", facts("Next", tag="button", kind="button"), point=None)
    assert call(page, "browser_click", {"ref": "e5"})["error"] == "unreadable"
    assert acted(page) == []


def test_a_plain_button_is_clicked_and_a_gate_click_needs_the_capability() -> None:
    page = Page()
    page.add("e5", facts("Next", tag="button", kind="button"))
    assert call(page, "browser_click", {"ref": "e5"})["ok"] is True
    assert acted(page) == [("click",)]
    page = Page()
    page.add("e5", facts("Continue", tag="div", kind=""), point={**SUBMIT, "inside": True})
    tab = tab_of(page)
    tab.gate = True
    assert call(page, "browser_click", {"ref": "e5"}, tab)["ok"] is True
    assert acted(page) == [("click",)]


def test_an_attach_button_whose_point_is_a_submit_is_refused() -> None:
    page = Page()
    page.add("e5", facts("Attach", tag="button", kind="button"), point={**SUBMIT, "inside": False})
    tab = tab_of(page)
    tab.state.files["cv.pdf"] = b"%PDF-1.7"
    result = call(page, "browser_file_upload", {"ref": "e5", "paths": ["cv.pdf"]}, tab)
    assert result["error"] == "submit"
    assert acted(page) == []


# --- 5. nothing of a page off the list is read or acted on -------------------------------------


def snapshot_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(page: Any) -> str:
        return "- textbox [ref=e5]: the page's text"

    monkeypatch.setattr(pages, "snapshot_for_ai", fake)


READING = [
    ("browser_snapshot", {}),
    ("browser_take_screenshot", {}),
    ("browser_take_screenshot", {"ref": "e5"}),
    ("browser_wait_for", {"time": 0}),
    ("browser_tabs", {}),
    ("browser_click", {"ref": "e5"}),
    ("browser_type", {"ref": "e5", "text": "a"}),
    ("browser_check", {"ref": "e5"}),
    ("browser_select_option", {"ref": "e5", "values": ["FR"]}),
    ("browser_press_key", {"key": "Enter"}),
    ("browser_navigate", {"url": ON}),
]


@pytest.mark.parametrize(("name", "args"), READING)
@pytest.mark.parametrize("where", ["page", "main frame"])
def test_a_page_off_the_list_answers_host_not_allowed_and_no_content(
    monkeypatch: pytest.MonkeyPatch, name: str, args: dict[str, Any], where: str
) -> None:
    snapshot_stub(monkeypatch)
    page = Page()
    if where == "page":
        page.url = OFF
    else:
        page.frame_url = OFF
    page.add("e5", EMAIL)
    result = call(page, name, args)
    assert result["ok"] is False
    assert result["error"] == "host_not_allowed"
    for key in ("snapshot", "data", "tabs", "title"):
        assert key not in result
    assert acted(page) == []
    assert result["url"] == ON  # taken back


def test_a_redirect_committing_during_a_read_is_not_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = Page()

    async def hop(p: Any) -> str:
        page.url = OFF  # the 3xx hop of a click commits while the snapshot is read
        return "- heading [ref=e1]: the inbox"

    monkeypatch.setattr(pages, "snapshot_for_ai", hop)
    result = call(page, "browser_snapshot", {})
    assert result["error"] == "host_not_allowed"
    assert "snapshot" not in result
    page.url = ON
    shot = Page()

    async def shoot(**kw: Any) -> bytes:
        shot.url = OFF
        return b"png of the inbox"

    shot.screenshot = shoot  # type: ignore[method-assign]
    result = call(shot, "browser_take_screenshot", {})
    assert result["error"] == "host_not_allowed"
    assert "data" not in result


class Hop:
    """A request event of the context, as Playwright gives it for a redirect."""

    def __init__(self, url: str, frame: Any, redirected: bool = True, nav: bool = True) -> None:
        self.url, self.frame = url, frame
        self.redirected_from = object() if redirected else None
        self.nav = nav

    def is_navigation_request(self) -> bool:
        return self.nav


def test_a_redirect_hop_off_the_list_is_stopped_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_stub(monkeypatch)
    page = Page()
    tab = tab_of(page)

    async def go() -> dict[str, Any]:
        await tab.page()
        hop = tab.browser.contexts[0].listeners["request"]
        main = page.main_frame
        sub = type("Sub", (), {"parent_frame": main})()
        hop(Hop(OFF, sub))  # a sub frame: B6's (follow up)
        hop(Hop(ON, main))  # a hop on the list
        hop(Hop(OFF, main, redirected=False))  # the first URL: the route guard's
        hop(Hop(OFF, main, nav=False))  # a sub resource
        assert tab.state.blocked == []
        hop(Hop(OFF, main))
        assert tab.state.blocked == [OFF]
        await asyncio.sleep(0)  # one turn of the loop (no wall clock): the scheduled stop runs
        assert ("stop",) in page.calls
        return await actions.run(tab, "browser_snapshot", {})

    result = asyncio.run(go())
    assert result["error"] == "host_not_allowed"
    assert "snapshot" not in result
    assert tab.state.blocked == []


def test_a_page_on_the_list_is_read() -> None:
    page = Page()
    result = call(page, "browser_take_screenshot", {})
    assert result["ok"] is True
    assert result["data"]


def test_the_end_of_the_lease_drops_the_documents() -> None:
    state = actions.State(files={"cv.pdf": b"%PDF-1.7"}, page=object(), blocked=[OFF])
    state.end()
    assert state.files == {}
    assert state.page is None
    assert state.blocked == []
