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

A discovery (browser B20, tools/discovery.py) is a sweep lease's website search: a normal browser
context (anonymous: a new one, JavaScript on, service workers allowed; signed in, B17: the
person's own persisted context), any public http or https address, at most three tabs, every
Playwright CLI command. Its line (tools/line.py) holds every path: `Tab.judge` reads each
click, Enter, key and typing through `line.control`, and a route on the context
(`Tab._line_route`) refuses a document POST that is no search and a POST whose address applies,
submits, writes to someone, pays, signs out or changes the account; every page of the context
bypasses service workers (CDP `Network.setBypassServiceWorker`), so the page's requests reach
the route while a worker may still register. browserd's egress proxy keeps every request to a
public address.

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
from urllib.parse import urlsplit

from browserd import detect, pages
from browserd.config import NAVIGATION_S
from browserd.tools import ask_person, discovery, line, pacing, policy, request_signin

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


HOST_LOGGED = re.compile(r"[a-z0-9.-]{1,253}")
KIND_LOGGED = re.compile(r"[^A-Za-z]")  # a resource type as a log line names it: letters only


def host_of(url: object) -> str:
    """The host of an address as a log line may name it (B19): lower case letters, digits, dots
    and hyphens only, else `unreadable`; never its path, query or user part."""
    try:
        host = urlsplit(url).hostname if isinstance(url, str) else None
    except ValueError:
        host = None
    return host if host and HOST_LOGGED.fullmatch(host) else "unreadable"


ROUTE_LOGGED = 40  # a discovery's refused requests logged per lease, at most


def _logged(value: object, size: int = 20) -> str:
    text = value if isinstance(value, str) else ""
    return re.sub(r"\W", "", text)[:size] or "-"


def described(facts: object) -> str:
    """One element's facts as a refusal's warning names them: its tag, role and type, never a
    name (a name is page text: a person's name, a message preview)."""
    if not isinstance(facts, dict):
        return "facts=unreadable"
    return (
        f"tag={_logged(facts.get('tag'))} role={_logged(facts.get('role'))} "
        f"type={_logged(facts.get('type'))}"
    )


def failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


MESSAGES = {
    "submit": "Refused: this element submits the form, which only the person's approval does.",
    "danger": "Refused: this element deletes, withdraws or pays, which no tool does.",
    "card_field": "Refused: a payment card field is never filled nor clicked.",
    "credential": "Refused: a password, a one time code or a sign in's user name is the person's.",
    "bad_selector": "Refused: a submit selector of this lease is not valid, so nothing is clicked.",
    "unreadable": "Refused: the element could not be read, so nothing is clicked.",
    "stale_ref": "This ref is not on the page any more: take a new snapshot.",
    "host_not_allowed": "Refused: this host is not on the list of this lease.",
    "refused_scheme": "Refused: only http and https pages are opened.",
    "bad_url": "Refused: this address cannot be opened.",
    "not_editable": "Refused: only a text field is typed into.",
    "signin_required": "The website asks to sign in again: the person signs in themselves.",
    # B20: the search's line (tools/line.py), whatever the tool.
    "line": "Refused: the search never applies, submits a form that is no search, writes to "
    "anyone, pays, signs out or changes the account. Go on another way.",
}


def refused(code: str) -> dict[str, Any]:
    return failure(code, MESSAGES.get(code, "Refused."))


class OffList(Exception):  # noqa: N818  a control-flow signal, not an error
    """The tab shows a host off the list (or a hop off it was stopped): `run` takes it back and
    answers host_not_allowed, whatever the tool had done or read."""


class PersonInput(Exception):  # noqa: N818  a control-flow signal
    """The person took back input during a paced action."""


# Added to the original exception of a RefusalRecoveryError, which `run` re-raises: browserd
# could not close a tab off the list or take the page back from an address off it.
GUARD_FAILED_NOTE = "browserd: a guard could not be restored"


class RefusalRecoveryError(Exception):
    """Keep a policy repair failure outside ordinary navigation normalization."""

    def __init__(self, original: Exception):
        super().__init__()
        self.original = original


class UnsupportedDiscovery(Exception):  # noqa: N818
    """A discovery execution boundary could not be established or was lost."""


@dataclass
class State:
    """What the tools keep for one lease (Lease.scratch): the placed documents and the tab."""

    files: dict[str, bytes] = field(default_factory=dict)
    page: Any = None
    guarded: Any = None  # the context the navigation guard is installed on
    blocked: list[str] = field(default_factory=list)
    discovery: bool = False
    scope: "discovery.Scope | None" = None
    native_context: Any = None
    native_cdp: Any = None
    native_failed: bool = False
    signed_in: bool = False  # B17: discovery in the person's own persisted context
    stopped_kind: str = ""  # the resource type of the navigation `blocked` holds, for the log
    route_logged: int = 0  # B20: the discovery's refused requests logged so far
    # A9: the file chooser the person opened during Apply myself, until a document answers it.
    chooser: Any = None
    chooser_id: int = 0

    def end(self) -> None:
        """The lease ended: its documents and its tab are dropped at once, not when the old
        context's route handler (which holds this state) is collected."""
        self.files.clear()
        self.blocked.clear()
        self.stopped_kind, self.route_logged = "", 0
        self.page = self.guarded = self.chooser = None
        self.native_context = self.native_cdp = self.scope = None


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
    discovery: bool = False
    input_allowed: Callable[[], bool] | None = None

    def require_input(self) -> None:
        if self.input_allowed is not None and not self.input_allowed():
            raise PersonInput

    @property
    def hosts(self) -> tuple[str, ...]:
        return self.request.hosts

    def allowed(self, url: str) -> bool:
        """On the lease's list; a discovery (B20): any http or https address (the egress proxy
        refuses a private one)."""
        if url == ABOUT_BLANK:
            return True
        if self.state.discovery:
            return discovery.public_url(url)
        return policy.url_verdict(url, self.hosts) is None

    def on_list(self, page: Any) -> bool:
        """The page and its main frame show a host of the list."""
        try:
            return self.allowed(page.url) and self.allowed(page.main_frame.url)
        except Exception:
            return False

    async def begin_discovery(self, scope: "discovery.Scope", name: str, args: dict) -> None:
        """The first call of a discovery makes its context: a navigation, on a browser whose
        pages are all blank (loaded pages may hold listeners: never upgraded). Anonymous: a new
        context, JavaScript on, service workers allowed, no download; signed in (B17): the
        person's own persisted context. Either way the route of the line (`_line_route`) holds
        every request from the first navigation on, and each page bypasses service workers so
        its requests reach that route (`_watch`). A step that fails ends the discovery."""
        if self.state.native_failed:
            raise UnsupportedDiscovery
        given = self.state.scope
        if given is not None:
            if given != scope:
                raise UnsupportedDiscovery
            return
        browser = self.browser.browser
        if name != "browser_navigate" or any(
            p.url != ABOUT_BLANK for c in browser.contexts for p in c.pages
        ):
            raise UnsupportedDiscovery
        self.state.discovery = True
        self.state.signed_in = scope.signed_in
        try:
            async with asyncio.timeout(ACTION_S):
                if scope.signed_in:
                    context = browser.contexts[0]
                else:
                    context = await browser.new_context(
                        java_script_enabled=True, service_workers="allow", accept_downloads=False
                    )
                self.state.native_context = self.state.guarded = context
                await context.route("**/*", self._line_route)
                context.on("request", self._hop)
                context.on("page", self.extra_page)
                page = context.pages[0] if context.pages else await context.new_page()
                self.state.page = page
                for other in list(context.pages):
                    if other is not page:
                        await other.close()
                await self._watch(page)
                self.state.scope = scope
        except Exception as exc:
            await self.close_discovery()
            raise UnsupportedDiscovery from exc

    async def _watch(self, page: Any) -> None:
        """Every request of the page reaches the route: its service workers are bypassed (one
        may still register and run; its own fetches, the site's code, are not seen: declared)."""
        client = await page.context.new_cdp_session(page)
        await client.send("Network.enable")
        await client.send("Network.setBypassServiceWorker", {"bypass": True})

    async def _line_route(self, route: Any, request: Any) -> None:
        """The request floor of the line (line.request_refused): a refused document of the
        tab's main frame is a stopped navigation (settle() says so); any other refused request
        fails alone and the page goes on without it. A refused document (a form's POST) is
        answered 204 No Content, which Chromium never commits, so the tab stays on the page it
        was on: an abort committed Chromium's own error page, `chrome-error://`, and the
        agent's next call was refused off the list (part B of B20, test_b20_native)."""
        # Fail closed: a request that cannot be read is refused, and nothing read below (its
        # kind, its address for the log) can keep it from being refused (part A of B20 found an
        # unbound `kind` there).
        kind = "unknown"
        try:
            frame = request.frame if request.is_navigation_request() else None
            main = frame is not None and frame.parent_frame is None
        except Exception:  # a service worker's request has no frame: not a document
            frame, main = None, False
        try:
            kind = "document" if frame is not None else str(request.resource_type or "")
            why = line.request_refused(request.method, request.url, kind)
        except Exception:
            why = "unreadable"
        if self.state.native_failed:
            why = why or "ended"
        if why is None:
            await route.fallback()
            return
        try:
            url = str(request.url)
        except Exception:
            url = ""
        if main and not self.state.blocked:
            self.state.blocked.append(url[:200] or "unreadable")
            self.state.stopped_kind = f"{kind}:{why}"
        if self.state.route_logged < ROUTE_LOGGED:
            self.state.route_logged += 1
            log.warning(
                "lease %s: a request refused by the line: %s host=%s kind=%s",
                self.lease_tag,
                why,
                host_of(url),
                KIND_LOGGED.sub("", kind)[:20] or "unknown",
            )
        if frame is not None:
            await route.fulfill(status=204, body="")
            return
        await route.abort("blockedbyclient")

    async def close_discovery(self) -> None:
        self.state.native_failed = True
        if self.state.signed_in:  # never the person's own context: the discovery tab only
            if self.state.page is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.state.page.close(), ACTION_S)
            return
        if self.state.native_context is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.state.native_context.close(), ACTION_S)

    async def extra_page(self, page: Any) -> None:
        """A tab the page or the agent opened: watched as the first one, closed past TABS_MAX
        (SPEC-browser: three tabs a session)."""
        try:
            if len(page.context.pages) > discovery.TABS_MAX:
                await asyncio.wait_for(page.close(), ACTION_S)
                return
            await self._watch(page)
        except Exception:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(page.close(), ACTION_S)

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
        # sees it, so its load is stopped here and settle() takes the tab back. A discovery's hop
        # meets its route again (a redirect is a new request there): any public address goes on.
        try:
            if request.redirected_from is None or not request.is_navigation_request():
                return
            frame = request.frame
            if frame.parent_frame is not None or self.allowed(request.url):
                return
        except Exception:
            return
        self.state.blocked.append(request.url[:200])
        self.state.stopped_kind = "redirect"
        asyncio.ensure_future(stop_loading(frame))  # noqa: RUF006  fire and forget, the page owns it

    async def guard(self) -> Any:
        """The context the lease's tabs live in, with the navigation guard installed on it once
        (a Chromium started again, browserd/handoff.py, is a new context: guarded again)."""
        if self.state.discovery:
            if self.state.native_context is None or self.state.native_failed:
                raise UnsupportedDiscovery
            return self.state.native_context
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
        self.state.chooser = chooser
        self.state.chooser_id += 1

    async def page(self) -> Any:
        context = await self.guard()
        current = self.state.page
        if self.state.discovery and (current is None or current.is_closed()):
            raise UnsupportedDiscovery
        if current is None or current.is_closed() or current not in context.pages:
            current = context.pages[0] if context.pages else await context.new_page()
            self.state.page = current
        page = await pages.front(current)
        if not self.on_list(page) or self.state.blocked:
            raise OffList
        return page

    async def settle(self, result: dict[str, Any]) -> dict[str, Any]:
        if self.state.native_failed:
            return failure(discovery.UNSUPPORTED, "Refused: native discovery is unavailable.")
        context = self.state.native_context or self.browser.browser.contexts[0]
        for other in list(context.pages):
            off = not self.allowed(other.url)
            if other is not self.state.page and off:
                log.warning("lease %s: a tab outside the allow list closed", self.lease_tag)
                try:
                    await other.close()
                except Exception as exc:
                    raise RefusalRecoveryError(exc) from exc
        page = self.state.page
        left = page is not None and not page.is_closed() and not self.on_list(page)
        if left or self.state.blocked:
            # The target's host only, for the next diagnosis (B19): never a path or a query;
            # and what was stopped (a document, a redirect hop: the resource type CDP or
            # Playwright gave, 2026-10-30).
            stopped = host_of(self.state.blocked[0] if self.state.blocked else page.url)
            kind = self.state.stopped_kind if self.state.blocked else "committed"
            try:
                if left:
                    with contextlib.suppress(Exception):  # no history, a timeout: the blank then
                        await page.go_back(timeout=ACTION_S * 1000)
                    if not self.on_list(page):
                        await page.goto(ABOUT_BLANK)
                self.state.blocked.clear()
                self.state.stopped_kind = ""
                log.warning(
                    "lease %s: a navigation outside the allow list stopped: host=%s kind=%s",
                    self.lease_tag,
                    stopped,
                    re.sub(r"[^A-Za-z:_]", "", kind)[:40] or "unknown",
                )
                if self.state.discovery:
                    return {**refused("line"), "url": page.url if page else None}
                return {**refused("host_not_allowed"), "url": page.url if page else None}
            except Exception as exc:
                raise RefusalRecoveryError(exc) from exc
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

    def judge(
        self, facts: Any, action: str, tool: str, args: dict | None = None
    ) -> dict[str, Any] | None:
        if self.discovery:
            code = line.control(facts, "key" if action == "key" else action)
            if not code:
                return None
            log.warning(
                "lease %s: %s refused by the line: %s %s",
                self.lease_tag,
                tool,
                code,
                described(facts),
            )
            return refused(code)
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
        self.require_input()
        await locator.scroll_into_view_if_needed(timeout=ACTION_S * 1000)
        self.require_input()
        await locator.hover(timeout=ACTION_S * 1000)
        await self.pacer.hover()
        self.require_input()


async def stop_loading(frame: Any) -> None:
    # Committed already, or the frame gone: settle() takes the tab back.
    with contextlib.suppress(Exception):
        await frame.evaluate("() => window.stop()")


async def run(tab: Tab, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """One tool on the lease's tab. Its arguments are checked first; then every reach of the page
    (Tab.page) checks the tab: off the list (a redirect that committed, a hop seen between calls)
    it is taken back and the call answers host_not_allowed before the tool acts or reads further.
    A result is answered only while the tab is still on the list (settle)."""
    marker = args.get("_discovery")
    if (
        tab.state.discovery
        and marker is None
        and name not in {"browser_snapshot", "browser_wait_for"}
    ):
        log.warning(
            "lease %s: %s refused: %s rule=no_marker", tab.lease_tag, name, discovery.UNSUPPORTED
        )
        return failure(
            "unsupported_discovery", "Refused: this lease requires discovery permission."
        )
    if marker is not None:
        if (
            marker != discovery.MARKER
            or tab.request.purpose != "sweep"
            or tab.gate
            or name not in discovery.TOOLS
        ):
            log.warning(
                "lease %s: %s refused: %s rule=not_a_sweep_tool",
                tab.lease_tag,
                name,
                discovery.UNSUPPORTED,
            )
            return failure(
                "unsupported_discovery", "Refused: discovery is limited to a search sweep."
            )
        scope = discovery.Scope.parse(args.get("_discovery_scope"))
    try:
        try:
            if marker is not None:
                if scope is None:
                    raise UnsupportedDiscovery
                await tab.begin_discovery(scope, name, args)
                tab.discovery = True
            result = await TOOLS[name](tab, args)
        except UnsupportedDiscovery:
            log.warning(
                "lease %s: %s refused: %s rule=boundary", tab.lease_tag, name, discovery.UNSUPPORTED
            )
            return failure(
                discovery.UNSUPPORTED, "Refused: this native discovery flow is unsupported."
            )
        except OffList:
            log.warning("lease %s: %s refused: host_not_allowed", tab.lease_tag, name)
            return await tab.settle(refused("host_not_allowed"))
        except PersonInput:
            return failure("person_input", "Refused: the person holds the input.")
        return await tab.settle(result) if result.get("ok") else result
    except RefusalRecoveryError as exc:
        # The original still propagates; its note tells routes.call_tool to answer guard_failed,
        # never the catch all's page_error (round 2 of PR 720).
        exc.original.add_note(GUARD_FAILED_NOTE)
        raise exc.original from None
    except Exception as exc:
        if name != "browser_navigate":
            raise
        from playwright.async_api import Error

        if not isinstance(exc, Error):
            raise
        return failure("navigation_failed", "The page did not let this navigation finish.")


async def close_off_list(tab: Any, hosts: tuple[str, ...], lease_tag: str) -> None:
    """A new tab stays only on the list or at the exact internal blank used by the memory
    guard. Navigation off the list is still aborted by the navigation guard."""
    with contextlib.suppress(Exception):
        await tab.wait_for_load_state("domcontentloaded", timeout=10_000)
    if (
        not tab.is_closed()
        and tab.url != ABOUT_BLANK
        and policy.url_verdict(tab.url, hosts) is not None
    ):
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
    return (
        {"chooser": True, "chooser_id": tab.state.chooser_id}
        if tab.state.chooser is not None
        else {}
    )


async def _here(tab: Tab) -> dict[str, Any]:
    page = await tab.page()
    return {"ok": True, "url": page.url, "title": await title(page), **_chooser(tab)}


async def snapshot(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    page = await tab.page()
    text, cut = policy.cut_snapshot(await pages.snapshot_for_ai(page))
    native_files = await pages.native_files(page, text)
    credentials = await pages.credentials(page, text)
    return {
        "ok": True,
        "url": page.url,
        "title": await title(page),
        "snapshot": text,
        "native_files": native_files,
        "credentials": credentials,
        "cut": cut,
        **_chooser(tab),
    }


async def navigate(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    code = policy.url_verdict(url, tab.hosts)
    if tab.state.discovery and code == "host_not_allowed" and discovery.public_url(url):
        code = None  # B20: any public address (the egress proxy refuses a private one)
    if code:
        log.warning(
            "lease %s: browser_navigate refused: %s host=%s", tab.lease_tag, code, host_of(url)
        )
        return refused(code)
    page = await tab.page()
    await tab.pacer.dwell()
    # Apply myself may open its form during PERSON_TIME. Automated navigation must still own
    # input after both pacing and the helper's awaited focus, immediately before goto.
    before_input = tab.require_input if tab.request.purpose != "apply_manual" else None
    result = await pages.navigate(page, args["url"], before_input=before_input)
    if result.get("ok"):
        await tab.pacer.read()
    elif tab.discovery:
        # Why a page of a search did not load: the failure, Chromium's net error if it named
        # one, and the target's host; never its path or query (a person's profile names them).
        found = NET_ERROR.search(str(result.get("detail") or ""))
        log.warning(
            "lease %s: browser_navigate failed: %s net=%s host=%s",
            tab.lease_tag,
            result.get("error"),
            found.group(0) if found else "-",
            host_of(url),
        )
    return await tab.settle(result)


NET_ERROR = re.compile(r"net::ERR_[A-Z_]{1,60}|interrupted by another navigation|Timeout")


async def click(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    button = args.get("button", "left")
    if button not in ("left", "right", "middle") or args.get("modifiers"):
        return failure("bad_args", "Only a plain click is allowed, with no modifier key.")
    locator, gone = await tab.element(args)
    if gone:
        return gone
    try:
        await tab.approach(locator)
    except Exception as exc:
        if not tab.discovery or type(exc).__name__ != "TimeoutError":
            raise
        return await not_ready(tab, locator, exc)
    facts = await tab.facts(locator, point=True)
    no = tab.judge(facts, "click", "browser_click")
    if no:
        return no
    tab.require_input()
    await locator.click(
        button=button,
        click_count=2 if args.get("doubleClick") else 1,
        timeout=ACTION_S * 1000,
    )
    return await tab.settle(await _here(tab))


# Round 2 (sweep of 2026-10-30, 14:42): the first click on Welcome to the Jungle's cookie
# banner failed with a bare TimeoutError: the element never became ready for the pointer within
# ACTION_S (Playwright's hover waits for it to be visible, stable and to receive the events), and
# neither the agent nor the log said why. A discovery's click that times out on the way to its
# element now answers why, from Playwright's own words (ACTIONABILITY, never the page's) and from
# where a click would land: another element there (an overlay, a banner, transparent or not) is
# `element_covered`, naming that element's tag and role in the log, never its text; else a
# `page_error` saying the element is hidden, moving or out of reach.
ACTIONABILITY = (
    ("intercepts pointer events", "covered"),
    ("not visible", "not_visible"),
    ("not stable", "moving"),
    ("outside of the viewport", "outside_viewport"),
    ("not enabled", "disabled"),
    ("detached", "detached"),
)
NOT_READY = (
    "The element did not become clickable: it is hidden, still moving or out of reach. Read the "
    "page again with browser_snapshot and try again or another way."
)


def actionability(message: object) -> str:
    """Why Playwright's wait for an element ran out, as one of the code's own words."""
    text = str(message)
    return next((reason for words, reason in ACTIONABILITY if words in text), "unknown")


BOX_COVERED = (
    "Another element covers this box, such as the button showing the place the website set, a "
    "list of suggestions or a cookie banner: click that button (or the box's clear button) "
    "first to open the box, or close the banner, then type again."
)


async def not_ready(
    tab: Tab, locator: Any, exc: Exception, tool: str = "browser_click"
) -> dict[str, Any]:
    """A discovery's click or typing whose element never became ready (above): covered, or not
    ready. A text box covered says what to click first (round 3, BOX_COVERED)."""
    reason = actionability(exc)
    facts = await tab.facts(locator, point=True)
    point = facts.get("point") if isinstance(facts, dict) else None
    covered = reason == "covered" or (isinstance(point, dict) and point.get("inside") is False)
    over = (
        f"{_logged(point.get('tag'))}/{_logged(point.get('role'))}"
        if isinstance(point, dict) and point.get("inside") is False
        else "-"
    )
    log.warning(
        "lease %s: %s not ready: reason=%s covered_by=%s",
        tab.lease_tag,
        tool,
        reason,
        over,
    )
    if covered and tool == "browser_type":
        return failure("element_covered", BOX_COVERED)
    if covered:
        return failure(
            "element_covered",
            "Another element covers this one, such as a cookie banner or a transparent layer "
            "over the page: close it first, then try again.",
        )
    return failure("page_error", NOT_READY)


# A card's links, in page order (CARD_LINKS at most, one more to tell a longer list).
FOCUSED_JS = """e => {
  let a = document.activeElement;
  for (let i = 0; a && a.shadowRoot && a.shadowRoot.activeElement && i < 50; i++) {
    a = a.shadowRoot.activeElement;
  }
  return a === e;
}"""


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
    # Round 3 (sweep of 2026-10-30, 16:08): LinkedIn's place box was covered (Playwright's
    # "intercepts pointer events") and the typing failed with a bare TimeoutError. In a signed
    # in search, a box covered by another element is focused instead of clicked, judged on its
    # own facts (no click lands anywhere), and typed in only while it holds the focus; any
    # other element not ready answers why (`not_ready`, BOX_COVERED).
    covered = False
    try:
        await tab.approach(locator)
    except Exception as exc:
        if not tab.discovery or type(exc).__name__ != "TimeoutError":
            raise
        if actionability(exc) != "covered":
            return await not_ready(tab, locator, exc, "browser_type")
        covered = True
    facts = await tab.facts(locator, point=not covered)
    no = tab.judge(facts, "type", "browser_type", args)
    if no is None and args.get("submit"):
        no = tab.judge(facts, "key", "browser_type", args)  # Enter after the text
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
        tab.require_input()
        # Check and setter share one synchronous browser task, with no CDP/await gap.
        result = await locator.evaluate(EMPTY_ONLY_JS, text)
        if not result.get("ok"):
            return refused(str(result.get("error") or "not_empty"))
        return await tab.settle(await _here(tab))
    tab.require_input()
    if covered:
        await locator.focus(timeout=ACTION_S * 1000)
        if await locator.evaluate(FOCUSED_JS, timeout=ACTION_S * 1000) is not True:
            log.warning("lease %s: browser_type covered: the box took no focus", tab.lease_tag)
            return failure("element_covered", BOX_COVERED)
        log.warning("lease %s: browser_type covered: typed through the focus", tab.lease_tag)
    else:
        await locator.click(timeout=ACTION_S * 1000)
    tab.require_input()
    await locator.fill("", timeout=ACTION_S * 1000)
    if covered and await locator.evaluate(FOCUSED_JS, timeout=ACTION_S * 1000) is not True:
        return failure("element_covered", BOX_COVERED)
    page = await tab.page()
    if len(text) >= pacing.PASTE_FROM:
        await tab.pacer.dwell()
        tab.require_input()
        await page.keyboard.insert_text(text)
    else:
        for char, delay in zip(text, tab.pacer.key_delays_s(len(text)), strict=True):
            tab.require_input()
            if char in ("\n", " "):
                # Inserted, no key pressed: a newline is no Enter, and a space no Space on a
                # button the page moved the focus to.
                await page.keyboard.insert_text(char)
            else:
                await page.keyboard.type(char)
            await tab.pacer.sleep(delay)
    if args.get("submit"):
        tab.require_input()
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
    no = tab.judge(await tab.facts(locator), "check", "browser_select_option", args)
    if no:
        return no
    await tab.approach(locator)
    tab.require_input()
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
    tab.require_input()
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
        chooser = tab.state.chooser
        if chooser is None:
            return failure("no_chooser", "No file chooser is open.")
        if type(args.get("chooser_id")) is not int or args["chooser_id"] != tab.state.chooser_id:
            return failure(
                "chooser_changed", "The file chooser changed. Choose the document again."
            )
        await tab.pacer.dwell()
        tab.require_input()
        if tab.state.chooser is not chooser:
            return failure(
                "chooser_changed", "The file chooser changed. Choose the document again."
            )
        await chooser.set_files(payloads, timeout=ACTION_S * 1000)
        if tab.state.chooser is chooser:
            tab.state.chooser = None
        return await tab.settle({**await _here(tab), "uploaded": names})
    locator, gone = await tab.element(args)
    if gone:
        return gone
    facts = await tab.facts(locator)
    if isinstance(facts, dict) and facts.get("tag") == "input" and facts.get("type") == "file":
        await tab.pacer.dwell()
        tab.require_input()
        await locator.set_input_files(payloads, timeout=ACTION_S * 1000)
        return await tab.settle({**await _here(tab), "uploaded": names})
    # A button that opens the file chooser (an Attach button): clicked as any click is.
    await tab.approach(locator)
    no = tab.judge(await tab.facts(locator, point=True), "click", "browser_file_upload")
    if no:
        return no
    page = await tab.page()
    tab.require_input()
    if tab.state.chooser is not None:
        return failure(
            "chooser_changed", "A file chooser is already open. Choose its document first."
        )
    async with page.expect_file_chooser(timeout=ACTION_S * 1000) as chooser:
        tab.require_input()
        await locator.click(timeout=ACTION_S * 1000)
    opened = await chooser.value
    tab.require_input()
    if tab.request.purpose == "apply_manual" and tab.state.chooser is not opened:
        return failure("chooser_changed", "The file chooser changed. Choose the document again.")
    await opened.set_files(payloads, timeout=ACTION_S * 1000)
    if tab.state.chooser is opened:
        tab.state.chooser = None
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
    tab.require_input()
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
    context = tab.state.native_context if tab.discovery else tab.browser.browser.contexts[0]
    found = list(context.pages)
    if action in ("select", "close"):
        index = args.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(found):
            return failure("bad_args", "index must be the index of an open tab.")
        tab.require_input()
        if action == "select":
            selected = await pages.front(found[index])
            tab.require_input()
            tab.state.page = selected
        elif len(found) > 1:
            closing = found[index]
            await closing.close()
            if closing is tab.state.page:  # the tab acted on is gone: the first one then
                tab.state.page = next(p for p in context.pages)
        found = list(context.pages)
    elif action == "new" and tab.discovery:
        # B20: a discovery opens a tab of its own, at most TABS_MAX, blank until it navigates.
        if len(found) >= discovery.TABS_MAX:
            return failure("too_many_tabs", f"At most {discovery.TABS_MAX} tabs are open.")
        tab.require_input()
        tab.state.page = await context.new_page()
        found = list(context.pages)
    elif action != "list":
        return failure("bad_args", "action is list, new, select or close.")
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


async def navigate_history(tab: Tab, args: dict[str, Any], back: bool) -> dict[str, Any]:
    """browser_navigate_back and browser_navigate_forward (B20): the tab's history, paced as a
    navigation; the page it lands on meets the route of the line as any load. Waited until the
    entry commits: a page Chromium restores from its back and forward cache fires no
    DOMContentLoaded, so Playwright's "domcontentloaded" timed out on every such Back (part B of
    B20, measured on real Chromium, test_b20_native)."""
    page = await tab.page()
    place = await history_of(page)
    if place is not None and not (place[0] > 0 if back else place[0] < place[1] - 1):
        return failure("no_history", "There is no page to go to in this tab's history.")
    await tab.pacer.dwell()
    tab.require_input()
    try:
        moved = await (page.go_back if back else page.go_forward)(
            timeout=NAVIGATION_S * 1000, wait_until="commit"
        )
    except Exception as exc:
        # Why, as navigate logs it: the error's type and Chromium's net error, never the address.
        found = NET_ERROR.search(str(exc))
        log.warning(
            "lease %s: %s failed: %s net=%s",
            tab.lease_tag,
            "browser_navigate_back" if back else "browser_navigate_forward",
            type(exc).__name__,
            found.group(0) if found else "-",
        )
        return failure("navigation_failed", "The page did not let this navigation finish.")
    if moved is None and place is None:  # a restore from the cache answers None as well
        return failure("no_history", "There is no page to go to in this tab's history.")
    await tab.pacer.read()
    return await tab.settle(await _here(tab))


async def history_of(page: Any) -> tuple[int, int] | None:
    """The tab's place in its history and the number of its entries (CDP), None when unread:
    Playwright's go_back answers None both with no entry and for a page restored from the back
    and forward cache, so it cannot tell them apart."""
    try:
        client = await page.context.new_cdp_session(page)
        try:
            found = await asyncio.wait_for(client.send("Page.getNavigationHistory"), ACTION_S)
        finally:
            with contextlib.suppress(Exception):
                await client.detach()
        return int(found["currentIndex"]), len(found["entries"])
    except Exception:
        return None


async def navigate_back(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    return await navigate_history(tab, args, back=True)


async def navigate_forward(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    return await navigate_history(tab, args, back=False)


async def hover(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    """browser_hover (B20): the pointer over an element, the way a hand gets there."""
    locator, gone = await tab.element(args)
    if gone:
        return gone
    await tab.approach(locator)
    return await tab.settle(await _here(tab))


SCROLL_MAX = 20_000  # pixels one scroll moves at most, either way


async def scroll(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    """browser_scroll (B20): the page, or the element `ref` names, by `dy` (down when positive)
    and `dx` pixels; a wheel over the element, so a list that loads more on scroll does."""
    dx, dy = args.get("dx", 0), args.get("dy", 600)
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and abs(v) <= SCROLL_MAX for v in (dx, dy)
    ):
        return failure("bad_args", f"dx and dy are whole pixels, at most {SCROLL_MAX} either way.")
    page = await tab.page()
    await tab.pacer.dwell()
    if args.get("ref"):
        locator, gone = await tab.element(args)
        if gone:
            return gone
        await tab.approach(locator)
    tab.require_input()
    await page.mouse.wheel(dx, dy)
    await tab.pacer.hover()
    return await tab.settle(await _here(tab))


SCRIPT_MAX = 10_000  # characters of the agent's script
RESULT_MAX = 20_000  # characters of its result returned


async def evaluate(tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
    """browser_evaluate (B20): the agent's own script on the page, `() => ...`, its result as
    JSON text (RESULT_MAX). It runs inside
    line.SCRIPT_GUARD_JS, which refuses `element.click()`, `form.submit()` and
    `form.requestSubmit()` on what the line refuses; whatever it sends otherwise meets the route
    of the line (a document POST that is no search, a POST to an apply, message or account
    address are refused). What browserd cannot see in a script: an event it builds and
    dispatches itself, a WebSocket, a worker it starts; the request floor holds what leaves."""
    if not tab.discovery:
        return failure("bad_args", "browser_evaluate runs in a website search only.")
    source = args.get("function")
    if not isinstance(source, str) or not 0 < len(source) <= SCRIPT_MAX:
        return failure("bad_args", f"function is a script of at most {SCRIPT_MAX} characters.")
    page = await tab.page()
    await tab.pacer.dwell()
    tab.require_input()
    try:
        async with asyncio.timeout(policy.WAIT_MAX_S):
            text = await page.evaluate(line.SCRIPT_GUARD_JS, [source, line.SCRIPT_RULES])
    except TimeoutError:
        return failure("timeout", f"The script ran past {policy.WAIT_MAX_S:g} seconds.")
    except Exception as exc:
        message = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
        refused_line = "browserd: refused by the line" in message
        return failure(
            "line" if refused_line else "script_error",
            MESSAGES["line"] if refused_line else f"The script failed: {message}",
        )
    text = text if isinstance(text, str) else "null"
    result = {"ok": True, "result": text[:RESULT_MAX], "cut": len(text) > RESULT_MAX}
    settled = await tab.settle(await _here(tab))
    return {**settled, **result} if settled.get("ok") else settled


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
    # B20: the rest of the Playwright CLI set, for a website search.
    "browser_navigate_back": navigate_back,
    "browser_navigate_forward": navigate_forward,
    "browser_hover": hover,
    "browser_scroll": scroll,
    "browser_evaluate": evaluate,
    "browser_ask_person": ask_person.run,  # browserd/tools/ask_person.py
}
