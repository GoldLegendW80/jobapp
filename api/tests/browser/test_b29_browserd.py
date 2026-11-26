"""Browser B29 in browserd: a website search goes on after its tab was closed.

Production audit of 2026-11-26: after the signed in LinkedIn search's first navigation, every
later call answered `unsupported_discovery` (rule boundary) and every snapshot "The page could
not be read.": `Tab.page()` refused a discovery whose tab was closed, most likely by the memory
guard. Now the next call opens a blank page in the lease's own context (the route of the line,
the context's listeners and the service worker bypass as the first page had them) and answers
`tab_closed` with the reason code knows: the guard's own record (`memory`, with the working set),
the page's crash event (`crash`), else `other`. After a close for memory the context loads no
image, media or font. A changed scope and a failed start stay refused.

Only the Playwright and CDP boundary is fake (test_b17_browserd's shape, with pages a context
removes when they close, as Playwright's do). Invented hosts only.
"""

import asyncio
import importlib
import logging
import re
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

from browserd import chromium  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import Request  # noqa: E402
from browserd.tools import actions, discovery, pacing  # noqa: E402

START = "https://jobs.example-robotics.test/search"
POSTING = "https://jobs.example-robotics.test/jobs/42"
SCOPE = {"start": START}
LIMIT = 2048 << 20
MARK = {"_discovery": discovery.MARKER, "_discovery_scope": SCOPE}
SIGNED_IN = {"_discovery": discovery.MARKER, "_discovery_scope": {**SCOPE, "signed_in": True}}


class Cdp:
    def __init__(self, sent: list) -> None:
        self.sent = sent

    async def send(self, method: str, params: Any = None) -> dict[str, Any]:
        self.sent.append((method, params))
        return {}


class Locator:
    async def count(self) -> int:
        return 0  # a ref of the page before: gone


class Page:
    def __init__(self, context: "Context", url: str = actions.ABOUT_BLANK) -> None:
        self.context, self.url, self.closed = context, url, False
        self.listeners: dict[str, list[Any]] = {}
        self.scripts: list[str] = []

    @property
    def main_frame(self) -> Any:
        return SimpleNamespace(url=self.url)

    def on(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def close(self) -> None:
        self.closed = True
        if self in self.context.pages:
            self.context.pages.remove(self)
        if self.context.browser is not None:
            self.context.browser.page_closed()

    def is_closed(self) -> bool:
        return self.closed

    async def bring_to_front(self) -> None:
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")

    async def goto(self, url: str, **_: Any) -> Any:
        self.url = url
        return SimpleNamespace(status=200)

    async def title(self) -> str:
        return "Robot jobs | Example Robotics"

    def locator(self, _: str) -> Locator:
        return Locator()

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.scripts.append(script)
        return '"read"'


class Context:
    def __init__(self, pages: int = 1, fail_new_page: bool = False, fail_cdp: int = 0) -> None:
        self.pages: list[Page] = []
        self.pages.extend(Page(self) for _ in range(pages))
        self.routes: list[tuple[str, Any]] = []
        self.listeners: dict[str, list[Any]] = {}
        self.cdp_sent: list[tuple[str, Any]] = []
        self.watched: list[Page] = []  # the pages a CDP session was opened on
        self.fail_new_page, self.fail_cdp = fail_new_page, fail_cdp
        self.closed = False
        self.browser: Browser | None = None

    async def route(self, pattern: str, handler: Any) -> None:
        self.routes.append((pattern, handler))

    def on(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def new_cdp_session(self, page: Page) -> Cdp:
        if self.fail_cdp:
            self.fail_cdp -= 1
            raise RuntimeError("Protocol error (Target.attachToTarget)")
        self.watched.append(page)
        return Cdp(self.cdp_sent)

    async def new_page(self) -> Page:
        if self.fail_new_page or (self.browser is not None and self.browser.gone):
            raise RuntimeError("Target page, context or browser has been closed")
        page = Page(self)
        self.pages.append(page)
        for listener in self.listeners.get("page", []):
            await listener(page)  # Tab.extra_page, as Playwright's "page" event calls it
        return page

    async def close(self) -> None:
        self.closed = True


class Browser:
    """Chromium as browserd runs it: when its last page closes, every context's counted, it
    quits, and every later new_page raises (measured on Chromium 156 by the review of PR 839)."""

    def __init__(self, contexts: list[Context], made: Context | None = None) -> None:
        self.contexts: list[Context] = []
        self.made, self.gone = made, False
        for context in contexts:
            self.adopt(context)

    def adopt(self, context: Context) -> Context:
        context.browser = self
        self.contexts.append(context)
        return context

    async def new_context(self, **_: Any) -> Context:
        assert self.made is not None
        return self.adopt(self.made)

    def page_closed(self) -> None:
        self.gone = self.gone or not any(c.pages for c in self.contexts)


async def no_sleep(_: float) -> None:
    return None


def started(signed_in: bool = False, **context: Any) -> tuple[actions.Tab, Context, Any]:
    """A sweep lease's tab with its discovery begun on START: (tab, its context, the browser
    the memory guard reads: the person's context with its blank page, then the search's)."""
    request = Request.parse(
        {
            "user_id": "e2e-b29",
            "profile_id": "1b29c0de-0000-4000-8000-00000000b029",
            "uid": 20029,
            "purpose": "sweep",
            "hosts": ["jobs.example-robotics.test"],
        }
    )
    person = Context(**context) if signed_in else Context()
    made = person if signed_in else Context(**context)
    browser = chromium.Chromium(request, "b29-lease", None, Config(secret=b"s"))
    browser.browser = Browser([person], made)
    tab = actions.Tab(
        request=request,
        state=actions.State(),
        browser=browser,
        pacer=pacing.Pacer(no_sleep),
        lease_tag="b29-test",
        person_holds=lambda: False,
    )
    scope = discovery.Scope.parse({**SCOPE, "signed_in": signed_in})
    asyncio.run(tab.begin_discovery(scope, "browser_navigate", {"url": START}))
    return tab, made, browser


def call(tab: actions.Tab, name: str, args: dict[str, Any], mark: Any = MARK) -> dict[str, Any]:
    """One tool call as routes.call_tool makes it: a Tab of its own on the lease's state."""
    fresh = actions.Tab(
        request=tab.request,
        state=tab.state,
        browser=tab.browser,
        pacer=tab.pacer,
        lease_tag=tab.lease_tag,
        person_holds=tab.person_holds,
    )
    marked = name not in ("browser_snapshot", "browser_wait_for")
    return asyncio.run(actions.run(fresh, name, {**args, **mark} if marked else dict(args)))


def guard(browser: Any, used: int = 1850, owner: Any = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    closed = asyncio.run(
        chromium.guard_memory(
            browser.browser,
            90,
            lambda **event: events.append(event),
            lambda: (used << 20, LIMIT),
            owner=owner,
        )
    )
    assert closed
    return events


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # The API environment has no Playwright: its two error types are pages.navigate's only need.
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(Error=RuntimeError, TimeoutError=TimeoutError),
    )

    async def snapshot_for_ai(page: Any) -> str:
        return f'- heading "Robot jobs at {page.url}" [level=1]'

    async def nothing(*_: Any) -> list:
        return []

    monkeypatch.setattr(actions.pages, "snapshot_for_ai", snapshot_for_ai)
    monkeypatch.setattr(actions.pages, "native_files", nothing)
    monkeypatch.setattr(actions.pages, "credentials", nothing)


TOOLS = {
    "browser_navigate": {"url": POSTING},
    "browser_click": {"ref": "e7"},
    "browser_type": {"ref": "e8", "text": "Robot Engineer"},
    "browser_snapshot": {},
    "browser_evaluate": {"function": "() => document.title"},
}


@pytest.mark.parametrize("name", list(TOOLS))
def test_a_tab_the_memory_guard_closed_opens_again_and_the_search_goes_on(name: str) -> None:
    tab, made, browser = started()
    assert call(tab, "browser_navigate", {"url": START})["ok"]
    first = tab.state.page
    line_route = made.routes[0]
    events = guard(browser)
    assert first.closed  # the newest tab: the search's own
    assert made.pages == []
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1850}]

    bypassed = made.cdp_sent.count(("Network.setBypassServiceWorker", {"bypass": True}))
    told = call(tab, name, TOOLS[name])
    assert told["ok"] is False
    assert told["error"] == "tab_closed"
    assert "rule" not in told  # never a refusal, never `boundary`
    assert "does not open" not in told["message"]
    close = told["tab_closed"]
    assert (close["reason"], close["used_mib"], close["media_blocked"]) == ("memory", 1850, True)
    # A blank page of the lease's own context, watched as the first one was.
    [again] = made.pages
    assert tab.state.page is again
    assert again.url == actions.ABOUT_BLANK
    assert made.routes[0] == line_route  # the route of the line, unchanged
    assert again in made.watched
    assert made.cdp_sent.count(("Network.setBypassServiceWorker", {"bypass": True})) > bypassed
    assert again.listeners["crash"]
    assert made.listeners["page"]  # the context's listeners, kept
    assert made.listeners["request"]
    assert not tab.state.native_failed

    if name != "browser_navigate":  # still blank: the same close, told again
        assert call(tab, name, TOOLS[name])["tab_closed"]["id"] == close["id"]
    moved = call(tab, "browser_navigate", {"url": POSTING})
    assert moved["ok"] is True
    assert again.url == POSTING
    assert moved["tab_closed"]["id"] == close["id"]  # counted once by its id in the api
    after = call(tab, name, TOOLS[name])
    assert "tab_closed" not in after
    assert after.get("error") != "unsupported_discovery"
    if name in ("browser_click", "browser_type"):
        assert after["error"] == "stale_ref"  # reached the page opened again
    else:
        assert after["ok"] is True
        assert after["url"] == POSTING


def test_a_crashed_tab_opens_again_with_its_reason_and_loads_its_media() -> None:
    tab, made, _ = started()
    call(tab, "browser_navigate", {"url": START})
    crashed = tab.state.page
    for listener in crashed.listeners["crash"]:
        listener(crashed)  # Playwright's "crash" event
    told = call(tab, "browser_click", {"ref": "e7"})
    assert told["error"] == "tab_closed"
    assert told["tab_closed"]["reason"] == "crash"
    assert "used_mib" not in told["tab_closed"]
    assert crashed.closed  # closed before another opens
    assert told["tab_closed"]["media_blocked"] is False
    assert len(made.routes) == 1  # no media left out after a crash


def test_a_tab_closed_by_anything_else_reads_other_never_a_guessed_cause() -> None:
    tab, made, _ = started()
    call(tab, "browser_navigate", {"url": START})
    asyncio.run(tab.state.page.close())
    told = call(tab, "browser_snapshot", {})
    assert told["error"] == "tab_closed"
    assert told["tab_closed"]["reason"] == "other"
    assert set(told["tab_closed"]) == {"id", "reason", "media_blocked"}
    assert len(made.routes) == 1


class Route:
    def __init__(self) -> None:
        self.outcome = ""

    async def abort(self, reason: str) -> None:
        self.outcome = reason

    async def fallback(self) -> None:
        self.outcome = "fallback"


def routed(handler: Any, kind: str) -> str:
    route = Route()
    request = SimpleNamespace(resource_type=kind, url=POSTING, method="GET")
    asyncio.run(handler(route, request))
    return route.outcome


def test_after_a_close_for_memory_no_image_media_or_font_loads() -> None:
    tab, made, browser = started()
    holds = [False]
    tab.person_holds = lambda: holds[0]
    call(tab, "browser_navigate", {"url": START})
    assert len(made.routes) == 1  # nothing left out before a first close (planner default)
    guard(browser)
    call(tab, "browser_navigate", {"url": POSTING})
    [(_, line), (pattern, lean)] = made.routes
    assert pattern == "**/*"
    assert line != lean  # registered after the line's, so run first
    for kind in ("image", "media", "font"):
        assert routed(lean, kind) == "blockedbyclient"
    for kind in ("document", "script", "stylesheet", "xhr", "fetch"):
        assert routed(lean, kind) == "fallback"  # to the line, which judges it as before
    holds[0] = True
    assert routed(lean, "image") == "fallback"  # the person's own view loads whole
    holds[0] = False
    # A second close for memory adds no second route.
    guard(browser)
    call(tab, "browser_snapshot", {})
    assert len(made.routes) == 2


def test_a_changed_scope_and_a_failed_start_stay_refused() -> None:
    tab, _, browser = started()
    call(tab, "browser_navigate", {"url": START})
    guard(browser)
    other = {"_discovery": discovery.MARKER, "_discovery_scope": {"start": POSTING}}
    refused = asyncio.run(actions.run(tab, "browser_click", {"ref": "e7", **other}))
    assert (refused["error"], refused["rule"]) == ("unsupported_discovery", "boundary")

    tab, _, browser = started(fail_new_page=True)
    call(tab, "browser_navigate", {"url": START})
    guard(browser)
    failed = call(tab, "browser_click", {"ref": "e7"})
    assert (failed["error"], failed["rule"]) == ("unsupported_discovery", "boundary")
    assert tab.state.native_failed  # the context could not give a page: ended, as a start


def test_the_tab_tools_act_and_name_the_close_so_the_app_gives_the_tab_back() -> None:
    tab, _, browser = started()
    call(tab, "browser_navigate", {"url": START})
    guard(browser)
    listed = call(tab, "browser_tabs", {"action": "list"})
    assert listed["ok"] is True
    assert listed["tab_closed"]["reason"] == "memory"
    assert [t["url"] for t in listed["tabs"]] == [actions.ABOUT_BLANK]


def test_the_persons_context_reuses_the_guards_blank_tab() -> None:
    """Signed in (B17), the search's tab is the person's context's only one: the guard opens a
    blank tab before it closes it, and that blank tab becomes the search's, watched."""
    tab, person, browser = started(signed_in=True)
    call(tab, "browser_navigate", {"url": START})
    guard(browser)
    [blank] = person.pages
    told = call(tab, "browser_snapshot", {})
    assert told["error"] == "tab_closed"
    assert person.pages == [blank]
    assert tab.state.page is blank
    assert blank.listeners["crash"]
    assert not person.closed


def test_a_signed_in_search_whose_only_tab_crashed_goes_on_and_chromium_stays_up() -> None:
    """Signed in, the search's tab is the browser's only page: a blank page opens before the
    crashed one closes, else Chromium quits and the lease ends (review of PR 839, Chromium 156)."""
    tab, person, browser = started(signed_in=True)
    assert call(tab, "browser_navigate", {"url": START}, SIGNED_IN)["ok"]
    crashed = tab.state.page
    assert [p for c in browser.browser.contexts for p in c.pages] == [crashed]
    for listener in crashed.listeners["crash"]:
        listener(crashed)
    told = call(tab, "browser_snapshot", {})
    assert (told["error"], told["tab_closed"]["reason"]) == ("tab_closed", "crash")
    assert "rule" not in told
    assert not browser.browser.gone
    assert crashed.closed
    [blank] = person.pages
    assert blank.url == actions.ABOUT_BLANK
    assert tab.state.page is blank
    assert blank in person.watched
    assert blank.listeners["crash"]
    assert not person.closed
    moved = call(tab, "browser_navigate", {"url": POSTING}, SIGNED_IN)
    assert moved["ok"] is True
    assert blank.url == POSTING


@pytest.mark.parametrize("failures", [1, 9])
def test_a_signed_in_search_that_ends_keeps_the_persons_window(failures: int) -> None:
    """A failed start of a signed in search closes its tab, the browser's only page: a blank
    page of the person's context opens first; when that blank page cannot stay (its watch fails
    too, so it is closed), the search's tab stays open rather than end Chromium."""
    request = Request.parse(
        {
            "user_id": "e2e-b29",
            "profile_id": "1b29c0de-0000-4000-8000-00000000b029",
            "uid": 20029,
            "purpose": "sweep",
            "hosts": ["jobs.example-robotics.test"],
        }
    )
    person = Context(fail_cdp=failures)
    browser = chromium.Chromium(request, "b29-lease", None, Config(secret=b"s"))
    browser.browser = Browser([person])
    tab = actions.Tab(
        request=request,
        state=actions.State(),
        browser=browser,
        pacer=pacing.Pacer(no_sleep),
        lease_tag="b29-test",
        person_holds=lambda: False,
    )
    [first] = person.pages
    scope = discovery.Scope.parse({**SCOPE, "signed_in": True})
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(scope, "browser_navigate", {"url": START}))
    assert not browser.browser.gone
    assert not person.closed
    assert tab.state.native_failed
    if failures == 1:
        assert first.closed
        assert [p.url for p in person.pages] == [actions.ABOUT_BLANK]
    else:
        assert person.pages == [first]


def test_a_signed_in_search_closing_a_tab_off_the_line_keeps_a_window() -> None:
    """settle() closes a tab of the search off the line; when it is the browser's last page (the
    search's own tab closed meanwhile), a blank page opens first."""
    tab, person, browser = started(signed_in=True)
    assert call(tab, "browser_navigate", {"url": START}, SIGNED_IN)["ok"]
    off = Page(person, "chrome-error://chromewebdata/")
    person.pages.append(off)
    asyncio.run(tab.state.page.close())
    asyncio.run(tab.settle({"ok": True}))
    assert off.closed
    assert not browser.browser.gone
    assert [p.url for p in person.pages] == [actions.ABOUT_BLANK]


def test_the_end_of_a_slot_keeps_a_window_for_the_other_slot() -> None:
    """Two browsers at once: the lease that started Chromium ends while the other slot's lease
    has no page open; its tabs close after a blank page opens, so Chromium stays up."""
    request = Request.parse(
        {
            "user_id": "e2e-b29",
            "profile_id": "1b29c0de-0000-4000-8000-00000000b029",
            "uid": 20029,
            "purpose": "sweep",
            "hosts": ["jobs.example-robotics.test"],
        }
    )
    shared = chromium.Chromium(request, "first-lease", None, Config(secret=b"s", slots=2))
    person, joined = Context(), Context(pages=0)
    shared.browser = Browser([person, joined])
    shared._homes["second-lease"] = joined
    [search] = person.pages
    asyncio.run(shared.end_slot("first-lease"))
    assert search.closed
    assert not shared.browser.gone
    assert [p.url for p in person.pages] == [actions.ABOUT_BLANK]
    assert not joined.closed


def test_the_memory_guard_names_the_lease_whose_tab_it_closed(caplog) -> None:
    request = Request.parse(
        {
            "user_id": "e2e-b29",
            "profile_id": "1b29c0de-0000-4000-8000-00000000b029",
            "uid": 20029,
            "purpose": "apply_agent",
            "hosts": ["jobs.example-robotics.test"],
        }
    )
    shared = chromium.Chromium(request, "first-lease", None, Config(secret=b"s", slots=2))
    person, search = Context(), Context()
    shared.browser = SimpleNamespace(contexts=[person, search])
    joined = SimpleNamespace(
        lease_id="second-lease", request=SimpleNamespace(purpose="sweep", slot=1)
    )
    shared.slot_of(joined)
    shared._extras["second-lease"] = [search]
    assert shared.owner(person) == ("apply_agent", 0)
    assert shared.owner(search) == ("sweep", 1)
    assert shared.owner(Context()) is None

    caplog.set_level(logging.WARNING, logger="browserd")
    events = guard(SimpleNamespace(browser=shared.browser), owner=shared.owner)
    assert events == [
        {"event": "tab_closed", "reason": "memory", "used_mib": 1850, "purpose": "sweep", "slot": 1}
    ]
    [line] = [r.getMessage() for r in caplog.records if "memory guard" in r.getMessage()]
    assert "rule=tab_closed_memory purpose=sweep slot=1 used_mib=1850 pct=90" in line
    # ops/jetson-ops.sh logs-browser counts `rule=[a-z_]{1,40}`: this line as one close.
    assert re.findall(r"rule=[a-z_]{1,40}", line) == ["rule=tab_closed_memory"]
    asyncio.run(shared.end_slot("second-lease"))
    assert shared.owner(search) is None

    alone = chromium.Chromium(request, "one-lease", None, Config(secret=b"s"))
    alone.browser = SimpleNamespace(contexts=[Context()])
    assert alone.owner(alone.browser.contexts[0]) == ("apply_agent", 0)


class Held:
    def __init__(self, slot: int) -> None:
        self.request = SimpleNamespace(slot=slot)
        self.events: list[dict[str, Any]] = []


class Leases:
    """Manager's view the routing reads: the running browser, the leases it serves, by id."""

    def __init__(self, browser: Any, held: dict[str, Held]) -> None:
        self.browser, self.by_id = browser, held

    def held(self) -> list[Held]:
        return list(self.by_id.values())

    def get(self, lease_id: str) -> Held | None:
        return self.by_id.get(lease_id)


def test_an_event_that_names_a_slot_goes_to_that_slots_lease_alone() -> None:
    main = importlib.import_module("browserd.__main__")
    running, other = object(), object()
    first, second = Held(0), Held(1)
    manager = Leases(running, {"first-lease": first, "second-lease": second})
    closed = {"event": "tab_closed", "reason": "memory", "used_mib": 1850, "slot": 1}
    main.route_event(manager, running, "first-lease", closed)
    assert (first.events, second.events) == ([], [closed])
    unnamed = {"event": "tab_closed", "reason": "tabs_max"}
    main.route_event(manager, running, "first-lease", unnamed)
    assert (first.events, second.events) == ([unnamed], [closed, unnamed])
    # Another browser's event (one a lease kept from before) goes to its own lease only.
    main.route_event(manager, other, "first-lease", {**closed, "slot": 0})
    assert len(first.events) == 2
    assert len(second.events) == 2
    main.route_event(manager, other, "gone-lease", unnamed)
    assert len(first.events) + len(second.events) == 4
