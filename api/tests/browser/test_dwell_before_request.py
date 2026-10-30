"""browserd's side of the pace the website receives (tests/browser/test_pace_received.py models
the api's side by hand from browserd/tools): every tool that can send a request to a website
waits its dwell (pacing.DWELL_S) before the first input that reaches the page, so the pause the
api counts from the end of the previous page is not eaten by the next page's own dwell, and a
later change of actions.py that sent a request first turns this red.

The tools run on the fake page of test_b5_actions, which records in order what reaches it and the
sleeps of browserd's pacer; the pacer draws the bottom of every range, the strictest case (a
hover of 0.2 s alone is no dwell of 1 s).
"""

import asyncio
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd.tools import actions, pacing  # noqa: E402

from tests.browser.test_b5_actions import ON, Browser, Context, Element, Page, facts  # noqa: E402

# What reaches the website (or the page's own scripts): the first of these is the request.
INPUTS = {
    "goto",
    "click",
    "fill",
    "key",
    "insert",
    "press",
    "set_checked",
    "select",
    "upload",
    "write",
}
# The tools that read the page, or are the app's own: none sends a request to the website.
READS = {
    "browser_snapshot",
    "browser_box",
    "browser_wait_for",
    "browser_take_screenshot",
    "browser_tabs",
    "browser_request_signin",
}


class PageError(Exception):
    pass


@pytest.fixture(autouse=True)
def playwright_errors(monkeypatch):
    """The API environment omits the browser image's Playwright (test_navigation_failures)."""
    try:
        import playwright.async_api  # noqa: F401
    except ModuleNotFoundError:
        errors = SimpleNamespace(Error=PageError, TimeoutError=PageError)
        monkeypatch.setitem(sys.modules, "playwright.async_api", errors)


class Bottom(random.Random):
    def uniform(self, a: float, b: float) -> float:
        return a


class Field(Element):
    """An element that records the inputs the b5 fake page leaves out."""

    async def select_option(self, values: list[str], **kw: Any) -> list[str]:
        self.page.calls.append(("select", tuple(values)))
        return values

    async def set_input_files(self, payloads: Any, **kw: Any) -> None:
        self.page.calls.append(("upload",))

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if js == actions.EMPTY_ONLY_JS:
            self.page.calls.append(("write", arg))
            return {"ok": True}
        return await super().evaluate(js, arg)


class Chooser:
    def __init__(self, page: "Site") -> None:
        self.page = page

    async def set_files(self, payloads: Any, **kw: Any) -> None:
        self.page.calls.append(("upload",))


class ChooserWait:
    """`page.expect_file_chooser()`: the click inside it opens the chooser."""

    def __init__(self, page: "Site") -> None:
        self.page = page
        self.value: asyncio.Future[Chooser] = asyncio.get_running_loop().create_future()

    async def __aenter__(self) -> "ChooserWait":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.value.set_result(Chooser(self.page))
        return False


class Site(Page):
    def add(self, ref: str, facts: dict[str, Any], **kw: Any) -> Element:
        self.elements[f"aria-ref={ref}"] = Field(self, facts, **kw)
        return self.elements[f"aria-ref={ref}"]

    async def goto(self, url: str, **kw: Any) -> None:
        self.calls.append(("goto", url))
        self.url = url

    def expect_file_chooser(self, **kw: Any) -> ChooserWait:
        return ChooserWait(self)


class Request:
    hosts = ("pages",)
    submit_selectors = ("#go",)
    purpose = "apply_fill"


class Editing(Request):
    purpose = "profile_edit"


class Timed(pacing.Pacer):
    """browserd's pacer, its sleeps and its dwells written into the page's record."""

    def __init__(self, page: Site) -> None:
        async def sleep(seconds: float) -> None:
            page.calls.append(("sleep", seconds))

        super().__init__(sleep, Bottom())
        self.page = page

    async def dwell(self) -> float:
        seconds = await super().dwell()
        self.page.calls.append(("dwell", seconds))
        return seconds


def run(
    name: str, args: dict[str, Any], target: dict[str, Any] | None, request: type = Request
) -> list[tuple[Any, ...]]:
    """One tool on a page with one element, e5 (`target`): what reached the page, in order."""
    page = Site()
    if target is not None:
        page.add("e5", target)
    context = Context(page)
    tab = actions.Tab(
        request=request(),
        state=actions.State(page=page, files={"cv.pdf": b"a pdf"}),
        browser=Browser(context),
        pacer=Timed(page),
    )
    result = asyncio.run(actions.run(tab, name, args))
    assert result["ok"] is True, result
    return page.calls


EMAIL = facts("Email", kind="email", field="email")
LETTER = facts("Letter", tag="textarea", kind="textarea")
NEXT = facts("Next", tag="button", kind="button")
CASES = {
    "navigate": ("browser_navigate", {"url": ON}, None, Request),
    "click": ("browser_click", {"ref": "e5"}, NEXT, Request),
    "type": ("browser_type", {"ref": "e5", "text": "ab"}, EMAIL, Request),
    "paste": ("browser_type", {"ref": "e5", "text": "x" * pacing.PASTE_FROM}, LETTER, Request),
    "type empty only": (
        "browser_type",
        {"ref": "e5", "text": "ab", "empty_only": True},
        EMAIL,
        Editing,
    ),
    "select": (
        "browser_select_option",
        {"ref": "e5", "values": ["fr"]},
        facts("Pays", tag="select", kind="select-one"),
        Request,
    ),
    "check": (
        "browser_check",
        {"ref": "e5"},
        facts("Remote", kind="checkbox"),
        Request,
    ),
    "upload to an input": (
        "browser_file_upload",
        {"ref": "e5", "paths": ["cv.pdf"]},
        facts("CV", kind="file"),
        Request,
    ),
    "upload by a button": (
        "browser_file_upload",
        {"ref": "e5", "paths": ["cv.pdf"]},
        facts("Attach", tag="button", kind="button"),
        Request,
    ),
    "press a key": ("browser_press_key", {"key": "Tab"}, None, Request),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_tool_that_sends_a_request_waits_its_dwell_first(case):
    calls = run(*CASES[case])
    first = next((i for i, c in enumerate(calls) if c[0] in INPUTS), None)
    assert first is not None, f"{case} reached the page with nothing: {calls}"
    dwells = [c[1] for c in calls[:first] if c[0] == "dwell"]
    assert dwells, f"{case} sent {calls[first]} before any dwell: {calls[: first + 1]}"
    assert min(dwells) >= pacing.DWELL_S[0]


def test_every_tool_is_either_paced_here_or_a_read():
    """A tool added to browserd/tools/actions.py has to be put in one list or the other."""
    paced = {name for name, *_ in CASES.values()}
    assert paced | READS == set(actions.TOOLS)
    assert not paced & READS
