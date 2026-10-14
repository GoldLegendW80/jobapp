"""What each tool does to the lease's page (browser B5), with Playwright MCP's tool names and
argument names. Every tool answers a result, `{"ok": true, ...}` or `{"ok": false, "error":
<code>, "message": <one sentence>}`; a refusal is a result, never an exception, so an agent reads
why. Arguments are checked before the page is touched; the page is then reached through
`Tab.page()` and every action ends with `Tab.settle()`: a tab on a host outside the lease's list
is closed, and the tab acted on is taken back (the navigation that left the list is reported).

Refs are those of the last `browser_snapshot` (Playwright's `aria-ref=eN` selector). A ref the
page no longer has (a re-render) answers `stale_ref`: nothing is clicked elsewhere.
"""

import asyncio
import base64
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from browserd import detect, pages
from browserd.tools import pacing, policy

log = logging.getLogger("browserd")

ACTION_S = 5.0  # an element's actionability (scroll, hover, click, fill), a failure result past it
SHOT_MAX = 8 * 1024 * 1024
ABOUT_BLANK = "about:blank"


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
}


def refused(code: str) -> dict[str, Any]:
    return failure(code, MESSAGES.get(code, "Refused."))


@dataclass
class State:
    """What the tools keep for one lease (Lease.scratch): the placed documents and the tab."""

    files: dict[str, bytes] = field(default_factory=dict)
    page: Any = None
    guarded: Any = None  # the context the navigation guard is installed on
    blocked: list[str] = field(default_factory=list)


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

    async def _guard(self, route: Any, request: Any) -> None:
        # A navigation of a tab's main frame to a host outside the list (a link, a redirect, a
        # window.open) never leaves: it is aborted here, and settle() reports it.
        frame = request.frame if request.is_navigation_request() else None
        if (
            frame is not None
            and frame.parent_frame is None
            and not self.allowed(request.url)
        ):
            self.state.blocked.append(request.url[:200])
            await route.abort("blockedbyclient")
            return
        await route.fallback()

    async def page(self) -> Any:
        context = self.browser.browser.contexts[0]
        if self.state.guarded is not context:
            await context.route("**/*", self._guard)
            hosts, tag = self.hosts, self.lease_tag
            context.on(
                "page",
                lambda tab: asyncio.ensure_future(close_off_list(tab, hosts, tag)),
            )
            self.state.guarded = context
        current = self.state.page
        if current is None or current.is_closed() or current not in context.pages:
            current = context.pages[0] if context.pages else await context.new_page()
            self.state.page = current
        return await pages.front(current)

    async def settle(self, result: dict[str, Any]) -> dict[str, Any]:
        context = self.browser.browser.contexts[0]
        for other in list(context.pages):
            off = policy.url_verdict(other.url, self.hosts) is not None
            if other is not self.state.page and off:
                log.warning(
                    "lease %s: a tab outside the allow list closed", self.lease_tag
                )
                await other.close()
        page = self.state.page
        left = page is not None and not page.is_closed() and not self.allowed(page.url)
        if left:
            with contextlib.suppress(
                Exception
            ):  # no history, a timeout: the blank page then
                await page.go_back(timeout=ACTION_S * 1000)
            if not self.allowed(page.url):
                await page.goto(ABOUT_BLANK)
        if left or self.state.blocked:
            self.state.blocked.clear()
            log.warning(
                "lease %s: a navigation outside the allow list stopped", self.lease_tag
            )
            return {**refused("host_not_allowed"), "url": page.url if page else None}
        return result

    async def element(self, args: dict[str, Any]) -> tuple[Any, Any]:
        """The element the ref names, with its facts; (None, failure) when it is gone."""
        ref = policy.ref_of(
            args
        )  # a malformed ref is bad_args before the page is touched
        page = await self.page()
        locator = page.locator(f"aria-ref={ref}")
        try:
            if await locator.count() != 1:
                return None, refused("stale_ref")
        except Exception:  # noqa: BLE001 - Playwright refuses a ref of an older snapshot
            return None, refused("stale_ref")
        return locator, None

    async def facts(self, target: Any) -> Any:
        try:
            return await target.evaluate(
                detect.FACTS_JS, list(self.request.submit_selectors)
            )
        except Exception:  # noqa: BLE001 - detached, navigated away: unreadable, fail closed
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
    except Exception:  # noqa: BLE001 - that navigation: no title this time
        return ""


async def _here(tab: Tab) -> dict[str, Any]:
    page = await tab.page()
    return {"ok": True, "url": page.url, "title": await title(page)}


async def snapshot(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    page = await tab.page()
    text, cut = policy.cut_snapshot(await pages.snapshot_for_ai(page))
    return {
        "ok": True,
        "url": page.url,
        "title": await title(page),
        "snapshot": text,
        "cut": cut,
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
        return failure(
            "bad_args", "Only a plain click is allowed, with no modifier key."
        )
    locator, gone = await tab.element(args)
    if gone:
        return gone
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator), "click", "browser_click")
    if no:
        return no
    await locator.click(
        button=button,
        click_count=2 if args.get("doubleClick") else 1,
        timeout=ACTION_S * 1000,
    )
    return await tab.settle(await _here(tab))


async def type_text(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str) or len(text) > policy.TEXT_MAX:
        return failure(
            "bad_args",
            f"text must be a string of at most {policy.TEXT_MAX} characters.",
        )
    locator, gone = await tab.element(args)
    if gone:
        return gone
    facts = await tab.facts(locator)
    no = tab.judge(facts, "type", "browser_type")
    if no is None and args.get("submit"):
        no = tab.judge(facts, "key", "browser_type")  # Enter after the text
    if no:
        return no
    await tab.approach(locator)
    await locator.click(timeout=ACTION_S * 1000)
    await locator.fill("", timeout=ACTION_S * 1000)
    page = await tab.page()
    if len(text) >= pacing.PASTE_FROM:
        await tab.pacer.dwell()
        await page.keyboard.insert_text(text)
    else:
        for char, delay in zip(text, tab.pacer.key_delays_s(len(text)), strict=True):
            await page.keyboard.type(char)
            await tab.pacer.sleep(delay)
    if args.get("submit"):
        await page.keyboard.press("Enter")
    return await tab.settle(await _here(tab))


async def select_option(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    values = args.get("values")
    if not isinstance(values, list) or not 0 < len(values) <= 20:
        return failure(
            "bad_args", "values must be a list of 1 to 20 option values or labels."
        )
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
    no = tab.judge(await tab.facts(locator), "check", "browser_check")
    if no:
        return no
    await locator.set_checked(checked, timeout=ACTION_S * 1000)
    return await tab.settle(await _here(tab))


async def file_upload(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    names = args.get("paths")
    if not isinstance(names, list) or not 0 < len(names) <= policy.FILES_MAX:
        return failure(
            "bad_args", "paths must list the names of documents given to this lease."
        )
    missing = [n for n in names if not isinstance(n, str) or n not in tab.state.files]
    if missing:
        log.warning(
            "lease %s: browser_file_upload refused: file_not_given", tab.lease_tag
        )
        return failure(
            "file_not_given", "Refused: only documents given to this lease upload."
        )
    payloads = [
        {"name": n, "mimeType": "application/pdf", "buffer": tab.state.files[n]}
        for n in names
    ]
    locator, gone = await tab.element(args)
    if gone:
        return gone
    facts = await tab.facts(locator)
    if (
        isinstance(facts, dict)
        and facts.get("tag") == "input"
        and facts.get("type") == "file"
    ):
        await tab.pacer.dwell()
        await locator.set_input_files(payloads, timeout=ACTION_S * 1000)
        return await tab.settle({**await _here(tab), "uploaded": names})
    # A button that opens the file chooser (an Attach button): clicked as any click is.
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator), "click", "browser_file_upload")
    if no:
        return no
    page = await tab.page()
    async with page.expect_file_chooser(timeout=ACTION_S * 1000) as chooser:
        await locator.click(timeout=ACTION_S * 1000)
    await (await chooser.value).set_files(payloads, timeout=ACTION_S * 1000)
    return await tab.settle({**await _here(tab), "uploaded": names})


async def press_key(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    key = args.get("key")
    if key not in policy.KEYS:
        return failure(
            "bad_args", f"key must be one of {', '.join(sorted(policy.KEYS))}."
        )
    page = await tab.page()
    if key in ("Enter", "Space"):
        focused = await page.evaluate_handle("document.activeElement")
        is_body = await focused.evaluate("e => !e || e === document.body")
        if not is_body:
            # Enter in a field of a form submits it; Enter or Space on a button presses it.
            action = "key" if key == "Enter" else "click"
            no = tab.judge(await tab.facts(focused), action, "browser_press_key")
            if no:
                return no
    await tab.pacer.dwell()
    await page.keyboard.press(" " if key == "Space" else key)
    return await tab.settle(await _here(tab))


async def wait_for(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    page = await tab.page()
    seconds, text, gone = args.get("time"), args.get("text"), args.get("textGone")
    if seconds is not None:
        if (
            not isinstance(seconds, int | float)
            or not 0 <= seconds <= policy.WAIT_MAX_S
        ):
            return failure(
                "bad_args", f"time is at most {policy.WAIT_MAX_S:g} seconds."
            )
        await tab.pacer.sleep(float(seconds))
    for wanted, state in ((text, "visible"), (gone, "hidden")):
        if wanted is None:
            continue
        if not isinstance(wanted, str) or not 0 < len(wanted) <= 500:
            return failure(
                "bad_args", "text and textGone are strings of at most 500 characters."
            )
        try:
            await page.get_by_text(wanted).first.wait_for(
                state=state, timeout=policy.WAIT_MAX_S * 1000
            )
        except Exception:  # noqa: BLE001 - Playwright's timeout
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
        return failure(
            "too_large", "The screenshot is too large: take the visible part only."
        )
    return {
        "ok": True,
        "type": kind,
        "url": page.url,
        "data": base64.b64encode(data).decode(),
    }


async def tabs(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action", "list")
    await tab.page()
    context = tab.browser.browser.contexts[0]
    found = list(context.pages)
    if action in ("select", "close"):
        index = args.get("index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(found)
        ):
            return failure("bad_args", "index must be the index of an open tab.")
        if action == "select":
            tab.state.page = await pages.front(found[index])
        elif len(found) > 1:
            await found[index].close()
        found = list(context.pages)
    elif action != "list":
        return failure(
            "bad_args", "action is list, select or close; no tool opens a new tab."
        )
    listed = [
        {
            "index": i,
            "url": p.url,
            "title": await p.title(),
            "current": p is tab.state.page,
        }
        for i, p in enumerate(found)
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
    "browser_wait_for": wait_for,
    "browser_take_screenshot": take_screenshot,
    "browser_tabs": tabs,
    # "browser_request_signin": request_signin.run,  # B12: browserd/tools/request_signin.py
}
