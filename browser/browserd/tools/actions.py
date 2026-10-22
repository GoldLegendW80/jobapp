"""What each tool does to the lease's page (browser B5), with Playwright MCP's tool names and
argument names. Every tool answers a result, `{"ok": true, ...}` or `{"ok": false, "error":
<code>, "message": <one sentence>}`; a refusal is a result, never an exception, so an agent reads
why. Arguments are checked before the page is touched; the page is then reached through
`Tab.page()` and every action ends with `Tab.settle()`: a tab on a host outside the lease's list
is closed, and the tab acted on is taken back (the navigation that left the list is reported).

Every tool runs through `run`: the tab's URL and its main frame's are checked against the list
each time the tool reaches the page (before it acts or reads), and again before a successful result
leaves, so no content of a page off the list is answered. A server side redirect (3xx) is not seen
by the route guard (Playwright calls a route handler for the first URL of a chain only): the
redirected request is caught by the context's request listener, which stops the load
(`window.stop()`) and records it, and settle() takes the tab back. That request has left by then
(declared open point: browserd cannot hold it through Playwright; its egress proxy, proxy.py,
could refuse a host off the lease's list, while B6's host rule drops private ranges only), but its
page is never read nor acted on.

Refs are those of the last `browser_snapshot` (Playwright's `aria-ref=eN` selector). A ref the
page no longer has (a re-render) answers `stale_ref`: nothing is clicked elsewhere.
"""

import asyncio
import base64
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from browserd import detect, pages
from browserd.tools import pacing, policy, request_signin

log = logging.getLogger("browserd")

ACTION_S = 5.0  # an element's actionability (scroll, hover, click, fill), a failure result past it
SHOT_MAX = 8 * 1024 * 1024
ABOUT_BLANK = "about:blank"
FRAME_DEPTH = 10  # frames followed to the focused element, refused past it
# A C0 or C1 control character, or DEL: typed key by key, a return presses Enter (and submits the
# form), a tab moves the focus. Refused, but for a newline into a multi line field (inserted).
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# The element keys go to: document.activeElement, followed down through open shadow roots.
DEEP_FOCUS_JS = """() => {
  let e = document.activeElement;
  for (let i = 0; e && e.shadowRoot && e.shadowRoot.activeElement && i < 50; i++) {
    e = e.shadowRoot.activeElement;
  }
  return e;
}"""
FOCUS_TAG_JS = "e => !e ? 'BODY' : (e === document.body || e === document.documentElement) ? 'BODY' : e.tagName"  # noqa: E501  one JS expression run in the page


def failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


MESSAGES = {
    "submit": "Refused: this element submits the form, which only the person's approval does.",
    "danger": "Refused: this element deletes, withdraws or pays, which no tool does.",
    "card_field": "Refused: a payment card field is never filled nor clicked.",
    "bad_selector": "Refused: a submit selector of this lease is not valid, so nothing is clicked.",
    "unreadable": "Refused: the element could not be read, so nothing is clicked.",
    "stale_ref": "This ref is not on the page any more: take a new snapshot.",
    "host_not_allowed": "Refused: this host is not on the list of this lease.",
    "refused_scheme": "Refused: only http and https pages are opened.",
    "bad_url": "Refused: this address cannot be opened.",
    "not_editable": "Refused: only a text field is typed into.",
}


def refused(code: str) -> dict[str, Any]:
    return failure(code, MESSAGES.get(code, "Refused."))


class OffList(Exception):  # noqa: N818  a control-flow signal, not an error
    """The tab shows a host off the list (or a hop off it was stopped): `run` takes it back and
    answers host_not_allowed, whatever the tool had done or read."""


@dataclass
class State:
    """What the tools keep for one lease (Lease.scratch): the placed documents and the tab."""

    files: dict[str, bytes] = field(default_factory=dict)
    page: Any = None
    guarded: Any = None  # the context the navigation guard is installed on
    blocked: list[str] = field(default_factory=list)
    # A9: the file chooser the person opened during Apply myself, until a document answers it.
    chooser: Any = None
    # The app's own click on an Attach button is waiting for its chooser: that one is not kept.
    opening: bool = False

    def end(self) -> None:
        """The lease ended: its documents and its tab are dropped at once, not when the old
        context's route handler (which holds this state) is collected."""
        self.files.clear()
        self.blocked.clear()
        self.page = self.guarded = self.chooser = None


@dataclass
class Tab:
    """One call's view of the lease: its request (hosts, selectors, purpose), its state, the
    Playwright browser, whether the call carries the gate's capability, and the pacer."""

    request: Any
    state: State
    browser: Any
    pacer: pacing.Pacer
    gate: bool = False
    lease_tag: str = ""

    @property
    def hosts(self) -> tuple[str, ...]:
        return self.request.hosts

    def allowed(self, url: str) -> bool:
        return url == ABOUT_BLANK or policy.url_verdict(url, self.hosts) is None

    def on_list(self, page: Any) -> bool:
        """The page and its main frame show a host of the list."""
        try:
            return self.allowed(page.url) and self.allowed(page.main_frame.url)
        except Exception:
            return False

    async def _guard(self, route: Any, request: Any) -> None:
        # A navigation of a tab's main frame to a host outside the list (a link, a redirect, a
        # window.open) never leaves: it is aborted here, and settle() reports it.
        frame = request.frame if request.is_navigation_request() else None
        if frame is not None and frame.parent_frame is None and not self.allowed(request.url):
            self.state.blocked.append(request.url[:200])
            await route.abort("blockedbyclient")
            return
        await route.fallback()

    def _hop(self, request: Any) -> None:
        # A redirect hop of a main frame navigation to a host off the list: the route guard never
        # sees it, so its load is stopped here and settle() takes the tab back.
        try:
            if request.redirected_from is None or not request.is_navigation_request():
                return
            frame = request.frame
            if frame.parent_frame is not None or self.allowed(request.url):
                return
        except Exception:
            return
        self.state.blocked.append(request.url[:200])
        asyncio.ensure_future(stop_loading(frame))  # noqa: RUF006  fire and forget, the page owns it

    async def guard(self) -> Any:
        """The context the lease's tabs live in, with the navigation guard installed on it once
        (a Chromium started again, browserd/handoff.py, is a new context: guarded again)."""
        context = self.browser.browser.contexts[0]
        if self.state.guarded is not context:
            await context.route("**/*", self._guard)
            context.on("request", self._hop)
            hosts, tag = self.hosts, self.lease_tag
            context.on(
                "page",
                lambda tab: asyncio.ensure_future(close_off_list(tab, hosts, tag)),
            )
            if self.request.purpose == "apply_manual":
                # A listener makes Playwright intercept every chooser: no dialog over a folder.
                for tab in context.pages:
                    tab.on("filechooser", self._chosen)
                context.on("page", lambda tab: tab.on("filechooser", self._chosen))
            self.state.guarded = context
        return context

    def _chosen(self, chooser: Any) -> None:
        if not self.state.opening:
            self.state.chooser = chooser

    async def page(self) -> Any:
        context = await self.guard()
        current = self.state.page
        if current is None or current.is_closed() or current not in context.pages:
            current = context.pages[0] if context.pages else await context.new_page()
            self.state.page = current
        page = await pages.front(current)
        if not self.on_list(page) or self.state.blocked:
            raise OffList
        return page

    async def settle(self, result: dict[str, Any]) -> dict[str, Any]:
        context = self.browser.browser.contexts[0]
        for other in list(context.pages):
            off = policy.url_verdict(other.url, self.hosts) is not None
            if other is not self.state.page and off:
                log.warning("lease %s: a tab outside the allow list closed", self.lease_tag)
                await other.close()
        page = self.state.page
        left = page is not None and not page.is_closed() and not self.on_list(page)
        if left:
            with contextlib.suppress(Exception):  # no history, a timeout: the blank page then
                await page.go_back(timeout=ACTION_S * 1000)
            if not self.on_list(page):
                await page.goto(ABOUT_BLANK)
        if left or self.state.blocked:
            self.state.blocked.clear()
            log.warning("lease %s: a navigation outside the allow list stopped", self.lease_tag)
            return {**refused("host_not_allowed"), "url": page.url if page else None}
        return result

    async def element(self, args: dict[str, Any]) -> tuple[Any, Any]:
        """The element the ref names, with its facts; (None, failure) when it is gone."""
        ref = policy.ref_of(args)  # a malformed ref is bad_args before the page is touched
        page = await self.page()
        locator = page.locator(f"aria-ref={ref}")
        try:
            if await locator.count() != 1:
                return None, refused("stale_ref")
        except Exception:
            return None, refused("stale_ref")
        return locator, None

    async def facts(self, target: Any, point: bool = False) -> Any:
        """FACTS_JS on the element; with `point`, the element a click lands on too (read after
        the approach, so at the place the click happens)."""
        try:
            return await target.evaluate(
                detect.FACTS_JS,
                {"sels": list(self.request.submit_selectors), "point": point},
            )
        except Exception:
            return None

    def judge(self, facts: Any, action: str, tool: str) -> dict[str, Any] | None:
        code = detect.verdict(
            facts, action=action, profile_edit=self.request.purpose == "profile_edit"
        )
        if code is None or (code == "submit" and self.gate and tool == "browser_click"):
            if code == "submit":
                log.info("lease %s: gate click on a submit element", self.lease_tag)
            return None
        log.warning("lease %s: %s refused: %s", self.lease_tag, tool, code)
        return refused(code)

    async def approach(self, locator: Any) -> None:
        """The paced way to an element: dwell, scroll into view, hover, a short pause."""
        await self.pacer.dwell()
        await locator.scroll_into_view_if_needed(timeout=ACTION_S * 1000)
        await locator.hover(timeout=ACTION_S * 1000)
        await self.pacer.hover()


async def stop_loading(frame: Any) -> None:
    # Committed already, or the frame gone: settle() takes the tab back.
    with contextlib.suppress(Exception):
        await frame.evaluate("() => window.stop()")


async def run(tab: Tab, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """One tool on the lease's tab. Its arguments are checked first; then every reach of the page
    (Tab.page) checks the tab: off the list (a redirect that committed, a hop seen between calls)
    it is taken back and the call answers host_not_allowed before the tool acts or reads further.
    A result is answered only while the tab is still on the list (settle)."""
    try:
        result = await TOOLS[name](tab, args)
    except OffList:
        log.warning("lease %s: %s refused: host_not_allowed", tab.lease_tag, name)
        return await tab.settle(refused("host_not_allowed"))
    return await tab.settle(result) if result.get("ok") else result


async def close_off_list(tab: Any, hosts: tuple[str, ...], lease_tag: str) -> None:
    """A tab the page opens (window.open, a target=_blank link) is closed unless it shows a page
    of the list once it has loaded (its navigation off the list was aborted by the guard)."""
    with contextlib.suppress(Exception):
        await tab.wait_for_load_state("domcontentloaded", timeout=10_000)
    if not tab.is_closed() and policy.url_verdict(tab.url, hosts) is not None:
        log.warning("lease %s: a tab outside the allow list closed", lease_tag)
        with contextlib.suppress(Exception):
            await tab.close()


async def title(page: Any) -> str:
    # A navigation under way (a redirect the page started) destroys the context title() reads.
    try:
        return await page.title()
    except Exception:
        return ""


def _chooser(tab: Tab) -> dict[str, Any]:
    return {"chooser": True} if tab.state.chooser is not None else {}


async def _here(tab: Tab) -> dict[str, Any]:
    page = await tab.page()
    return {"ok": True, "url": page.url, "title": await title(page), **_chooser(tab)}


async def snapshot(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    page = await tab.page()
    text, cut = policy.cut_snapshot(await pages.snapshot_for_ai(page))
    return {
        "ok": True,
        "url": page.url,
        "title": await title(page),
        "snapshot": text,
        "cut": cut,
        **_chooser(tab),
    }


async def navigate(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    code = policy.url_verdict(args.get("url"), tab.hosts)
    if code:
        log.warning("lease %s: browser_navigate refused: %s", tab.lease_tag, code)
        return refused(code)
    page = await tab.page()
    await tab.pacer.dwell()
    result = await pages.navigate(page, args["url"])
    if result.get("ok"):
        await tab.pacer.read()
    return await tab.settle(result)


async def click(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    button = args.get("button", "left")
    if button not in ("left", "right", "middle") or args.get("modifiers"):
        return failure("bad_args", "Only a plain click is allowed, with no modifier key.")
    locator, gone = await tab.element(args)
    if gone:
        return gone
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator, point=True), "click", "browser_click")
    if no:
        return no
    await locator.click(
        button=button,
        click_count=2 if args.get("doubleClick") else 1,
        timeout=ACTION_S * 1000,
    )
    return await tab.settle(await _here(tab))


EMPTY_ONLY_JS = """(element, text) => {
  const input = element instanceof HTMLInputElement;
  const textarea = element instanceof HTMLTextAreaElement;
  if ((!input && !textarea) || element.disabled || element.readOnly ||
      (input && !['text', 'search', 'tel', 'url', 'email', 'number'].includes(element.type)))
    return {ok: false, error: 'unsupported_empty_write'};
  if (element.value !== '') return {ok: false, error: 'not_empty'};
  const prototype = textarea ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, text);
  element.dispatchEvent(new Event('input', {bubbles: true}));
  element.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true};
}"""


async def type_text(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str) or len(text) > policy.TEXT_MAX:
        return failure(
            "bad_args",
            f"text must be a string of at most {policy.TEXT_MAX} characters.",
        )
    if CONTROL.search(text.replace("\n", "")):
        return failure(
            "bad_args",
            "text holds a control character (a tab, a return): none is typed.",
        )
    locator, gone = await tab.element(args)
    if gone:
        return gone
    # The field is clicked before the keys: judged as a text entry and at its click point.
    await tab.approach(locator)
    facts = await tab.facts(locator, point=True)
    no = tab.judge(facts, "type", "browser_type")
    if no is None and args.get("submit"):
        no = tab.judge(facts, "key", "browser_type")  # Enter after the text
    if no:
        return no
    if "\n" in text and not detect.multiline(facts):
        return failure(
            "bad_args",
            "A newline goes into a multi line field only, never a one line one.",
        )
    if "empty_only" in args and type(args["empty_only"]) is not bool:
        return failure("bad_args", "empty_only must be a boolean.")
    if args.get("empty_only"):
        if tab.request.purpose != "profile_edit" or args.get("submit"):
            return failure("bad_args", "An empty-only profile write cannot submit.")
        await tab.pacer.dwell()
        # Check and setter share one synchronous browser task, with no CDP/await gap.
        result = await locator.evaluate(EMPTY_ONLY_JS, text)
        if not result.get("ok"):
            return refused(str(result.get("error") or "not_empty"))
        return await tab.settle(await _here(tab))
    await locator.click(timeout=ACTION_S * 1000)
    await locator.fill("", timeout=ACTION_S * 1000)
    page = await tab.page()
    if len(text) >= pacing.PASTE_FROM:
        await tab.pacer.dwell()
        await page.keyboard.insert_text(text)
    else:
        for char, delay in zip(text, tab.pacer.key_delays_s(len(text)), strict=True):
            if char in ("\n", " "):
                # Inserted, no key pressed: a newline is no Enter, and a space no Space on a
                # button the page moved the focus to.
                await page.keyboard.insert_text(char)
            else:
                await page.keyboard.type(char)
            await tab.pacer.sleep(delay)
    if args.get("submit"):
        await page.keyboard.press("Enter")
    return await tab.settle(await _here(tab))


async def select_option(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    values = args.get("values")
    if not isinstance(values, list) or not 0 < len(values) <= 20:
        return failure("bad_args", "values must be a list of 1 to 20 option values or labels.")
    if not all(isinstance(v, str) and len(v) <= 500 for v in values):
        return failure("bad_args", "values must be strings.")
    locator, gone = await tab.element(args)
    if gone:
        return gone
    no = tab.judge(await tab.facts(locator), "check", "browser_select_option")
    if no:
        return no
    await tab.approach(locator)
    chosen = await locator.select_option(values, timeout=ACTION_S * 1000)
    return await tab.settle({**await _here(tab), "selected": chosen})


async def check(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    checked = args.get("checked", True)
    if not isinstance(checked, bool):
        return failure("bad_args", "checked must be true or false.")
    locator, gone = await tab.element(args)
    if gone:
        return gone
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator, point=True), "check", "browser_check")
    if no:
        return no
    await locator.set_checked(checked, timeout=ACTION_S * 1000)
    return await tab.settle(await _here(tab))


async def file_upload(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    names = args.get("paths")
    if not isinstance(names, list) or not 0 < len(names) <= policy.FILES_MAX:
        return failure("bad_args", "paths must list the names of documents given to this lease.")
    missing = [n for n in names if not isinstance(n, str) or n not in tab.state.files]
    if missing:
        log.warning("lease %s: browser_file_upload refused: file_not_given", tab.lease_tag)
        return failure("file_not_given", "Refused: only documents given to this lease upload.")
    payloads = [
        {"name": n, "mimeType": "application/pdf", "buffer": tab.state.files[n]} for n in names
    ]
    if not args.get("ref") and tab.request.purpose == "apply_manual":
        # A9: no element names the answer to the file chooser the person opened.
        chooser, tab.state.chooser = tab.state.chooser, None
        if chooser is None:
            return failure("no_chooser", "No file chooser is open.")
        await tab.pacer.dwell()
        await chooser.set_files(payloads, timeout=ACTION_S * 1000)
        return await tab.settle({**await _here(tab), "uploaded": names})
    locator, gone = await tab.element(args)
    if gone:
        return gone
    facts = await tab.facts(locator)
    if isinstance(facts, dict) and facts.get("tag") == "input" and facts.get("type") == "file":
        await tab.pacer.dwell()
        await locator.set_input_files(payloads, timeout=ACTION_S * 1000)
        return await tab.settle({**await _here(tab), "uploaded": names})
    # A button that opens the file chooser (an Attach button): clicked as any click is.
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator, point=True), "click", "browser_file_upload")
    if no:
        return no
    page = await tab.page()
    tab.state.opening = True
    try:
        async with page.expect_file_chooser(timeout=ACTION_S * 1000) as chooser:
            await locator.click(timeout=ACTION_S * 1000)
        opened = await chooser.value
    finally:
        tab.state.opening = False
    if tab.state.chooser is opened:
        tab.state.chooser = None
    await opened.set_files(payloads, timeout=ACTION_S * 1000)
    return await tab.settle({**await _here(tab), "uploaded": names})


async def press_key(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    key = args.get("key")
    if key not in policy.KEYS:
        return failure("bad_args", f"key must be one of {', '.join(sorted(policy.KEYS))}.")
    page = await tab.page()
    if key in ("Enter", "Space"):
        focused, tag = await focus_of(page)
        if focused is None:
            log.warning("lease %s: browser_press_key refused: unreadable", tab.lease_tag)
            return refused("unreadable")
        if tag != "BODY":
            # Enter in a field of a form submits it; Enter or Space on a button presses it.
            action = "key" if key == "Enter" else "click"
            no = tab.judge(await tab.facts(focused), action, "browser_press_key")
            if no:
                return no
    await tab.pacer.dwell()
    await page.keyboard.press(" " if key == "Space" else key)
    return await tab.settle(await _here(tab))


async def focus_of(page: Any) -> tuple[Any, str]:
    """The element a key goes to and its tag: the active element, followed through open shadow
    roots and into frames (a Greenhouse form framed by a company site). An iframe, a frame, an
    object or an embed whose content frame is out of reach is unresolved: (None, "") when the
    chain cannot be followed, so Enter and Space are refused (fail closed)."""
    frame: Any = page
    try:
        for _ in range(FRAME_DEPTH):
            handle = await frame.evaluate_handle(DEEP_FOCUS_JS)
            tag = await handle.evaluate(FOCUS_TAG_JS)
            if tag not in ("IFRAME", "FRAME", "OBJECT", "EMBED"):
                return handle, str(tag)
            frame = await handle.content_frame()
            if frame is None:
                return None, ""
    except Exception:
        return None, ""
    return None, ""


async def wait_for(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    page = await tab.page()
    seconds, text, gone = args.get("time"), args.get("text"), args.get("textGone")
    if seconds is not None:
        if not isinstance(seconds, int | float) or not 0 <= seconds <= policy.WAIT_MAX_S:
            return failure("bad_args", f"time is at most {policy.WAIT_MAX_S:g} seconds.")
        await tab.pacer.sleep(float(seconds))
    for wanted, state in ((text, "visible"), (gone, "hidden")):
        if wanted is None:
            continue
        if not isinstance(wanted, str) or not 0 < len(wanted) <= 500:
            return failure("bad_args", "text and textGone are strings of at most 500 characters.")
        try:
            await page.get_by_text(wanted).first.wait_for(
                state=state, timeout=policy.WAIT_MAX_S * 1000
            )
        except Exception:
            return failure("timeout", "The text did not appear or go within the wait.")
    return await tab.settle(await _here(tab))


async def take_screenshot(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    kind = args.get("type", "png")
    if kind not in ("png", "jpeg"):
        return failure("bad_args", "type is png or jpeg.")
    page = await tab.page()
    options: dict[str, Any] = {"type": kind, "timeout": 15_000}
    if kind == "jpeg":
        options["quality"] = 80
    if args.get("ref"):
        locator, gone = await tab.element(args)
        if gone:
            return gone
        data = await locator.screenshot(**options)
    else:
        data = await page.screenshot(full_page=bool(args.get("fullPage")), **options)
    if len(data) > SHOT_MAX:
        return failure("too_large", "The screenshot is too large: take the visible part only.")
    return {
        "ok": True,
        "type": kind,
        "url": page.url,
        "data": base64.b64encode(data).decode(),
    }


async def box(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    """The element's box in CSS pixels of the full page screenshot, and the device scale: for the
    app to outline a designated submit on its "before" screenshot (apply A8). Reads only."""
    locator, gone = await tab.element(args)
    if gone:
        return gone
    found = await locator.bounding_box(timeout=ACTION_S * 1000)
    if found is None:
        return refused("not_visible")
    page = await tab.page()
    x, y, scale = await page.evaluate("() => [window.scrollX, window.scrollY, devicePixelRatio]")
    return {
        "ok": True,
        "x": found["x"] + x,
        "y": found["y"] + y,
        "width": found["width"],
        "height": found["height"],
        "scale": scale,
    }


async def tabs(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action", "list")
    await tab.page()
    settled = await tab.settle({"ok": True})  # tabs off the list are closed before any is listed
    if not settled.get("ok"):
        return settled
    context = tab.browser.browser.contexts[0]
    found = list(context.pages)
    if action in ("select", "close"):
        index = args.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(found):
            return failure("bad_args", "index must be the index of an open tab.")
        if action == "select":
            tab.state.page = await pages.front(found[index])
        elif len(found) > 1:
            await found[index].close()
        found = list(context.pages)
    elif action != "list":
        return failure("bad_args", "action is list, select or close; no tool opens a new tab.")
    listed = [
        {
            "index": i,
            "url": p.url,
            "title": await p.title(),
            "current": p is tab.state.page,
        }
        for i, p in enumerate(found)
        if tab.on_list(p)
    ]
    return {"ok": True, "tabs": listed}


Tool = Callable[[Tab, dict[str, Any]], Awaitable[dict[str, Any]]]
TOOLS: dict[str, Tool] = {
    "browser_snapshot": snapshot,
    "browser_navigate": navigate,
    "browser_click": click,
    "browser_type": type_text,
    "browser_select_option": select_option,
    "browser_check": check,
    "browser_file_upload": file_upload,
    "browser_press_key": press_key,
    "browser_box": box,
    "browser_wait_for": wait_for,
    "browser_take_screenshot": take_screenshot,
    "browser_tabs": tabs,
    "browser_request_signin": request_signin.run,  # B12: browserd/tools/request_signin.py
}
