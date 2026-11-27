"""browserd's side of the code the person gives in the app (apply A25, owner 2026-11-26: "les
deux sont possibles"): the one exception of the credential rule. `detect.code_box` (accept and
refuse tables), the routes that keep it on the lease for one ask (`give_code`, `erase_code`, the
ask's binding of `agent_ask`), and `browser_type_code`: once, the lease's page of the ask's host,
a box of the top document the detector holds for a one time code box, the code never in an
answer, and masked in every answer while the box holds it."""

from __future__ import annotations

import json
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

from browserd import app, detect  # noqa: E402
from browserd.leases import LeaseError  # noqa: E402
from browserd.tools import actions, pacing, routes  # noqa: E402

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
        state.code = CODE
        state.end()
        assert (state.code, state.code_box, state.ask_id) == (None, None, "")

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


# --- the tool ---------------------------------------------------------------------------------


class Box:
    """A box of the fake page, and its element handle: its facts, its value and the focus."""

    def __init__(self, page: Page, facts: dict[str, Any]) -> None:
        self.page, self.facts, self.value, self.focused = page, facts, "", False

    async def count(self) -> int:
        return 1

    async def evaluate(self, script: str, arg: Any = None, **_: Any) -> Any:
        if script == detect.CODE_BOX_JS:
            return self.facts
        if script == actions.FOCUSED_JS:
            return self.focused
        if script == actions.VALUE_JS:
            return self.value if self.page.boxes else None
        raise AssertionError(script)

    async def focus(self, **_: Any) -> None:
        self.focused = True

    async def fill(self, text: str, **_: Any) -> None:
        self.value = text

    async def element_handle(self) -> Box:
        return self


class Keyboard:
    def __init__(self, page: Page) -> None:
        self.page = page

    async def type(self, char: str) -> None:
        box = self.page.boxes[0]
        if box.focused:
            box.value += char
        if self.page.steal_after is not None and len(box.value) >= self.page.steal_after:
            box.focused = False  # a box per character: the page moves the focus on


class Page:
    def __init__(self, url: str, facts: dict[str, Any], steal_after: int | None = None) -> None:
        self.url, self.steal_after = url, steal_after
        self.boxes = [Box(self, facts)]
        self.keyboard = Keyboard(self)


def tool_tab(purpose: str, page: Page, monkeypatch: Any, gate: bool = False) -> actions.Tab:
    async def nothing(_: float) -> None:
        return None

    tab = actions.Tab(
        request=SimpleNamespace(purpose=purpose, hosts=(), submit_selectors=()),
        state=actions.State(),
        browser=None,
        pacer=pacing.Pacer(sleep=nothing),
        gate=gate,
    )

    async def the_page(*_: Any, **__: Any) -> Page:
        return page

    async def element(args: dict[str, Any]) -> tuple[Any, Any]:
        actions.policy.ref_of(args)
        return page.boxes[0], None

    async def approach(_: Any) -> None:
        return None

    async def settle(result: dict[str, Any]) -> dict[str, Any]:
        return result

    async def here(_: Any) -> dict[str, Any]:
        return {"ok": True, "url": page.url, "title": "Verify"}

    async def snapshot(_: Any, __: Any) -> dict[str, Any]:
        shown = page.boxes[0].value if page.boxes else ""
        return {"ok": True, "url": page.url, "snapshot": f'- textbox "Code" [ref=e5]: {shown}'}

    async def screenshot(_: Any, __: Any) -> dict[str, Any]:
        return {"ok": True, "data": "png"}

    async def key(_: Any, __: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(tab, "page", the_page)
    monkeypatch.setattr(tab, "element", element)
    monkeypatch.setattr(tab, "approach", approach)
    monkeypatch.setattr(tab, "settle", settle)
    monkeypatch.setattr(actions, "_here", here)
    monkeypatch.setitem(actions.TOOLS, "browser_snapshot", snapshot)
    monkeypatch.setitem(actions.TOOLS, "browser_take_screenshot", screenshot)
    monkeypatch.setitem(actions.TOOLS, "browser_press_key", key)
    tab.state.code, tab.state.code_host = CODE, HOST
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
    assert page.boxes[0].value == ""
    assert CODE not in json.dumps(answer)
    again = run(actions.run(tab, "browser_type_code", {"ref": "e5"}))
    assert again["error"] == ("no_code" if purpose == "apply_agent" and not tab.gate else error)


@pytest.mark.parametrize("purpose", ["apply_agent", "apply_review"])
def test_the_code_is_typed_once_and_masked_while_the_box_holds_it(
    purpose: str, monkeypatch: Any
) -> None:
    page = Page(VERIFY, GOOD)
    tab = tool_tab(purpose, page, monkeypatch)
    answer = run(actions.run(tab, "browser_type_code", {"ref": "e5"}))
    assert answer == {"ok": True, "url": VERIFY, "title": "Verify", "code_typed": True}
    assert page.boxes[0].value == CODE
    assert tab.state.code is None
    assert run(actions.run(tab, "browser_type_code", {"ref": "e5"}))["error"] == "no_code"
    # The page shows it in the box, and in its address when its form is sent by GET: masked.
    page.url = f"{VERIFY}?code={CODE}"
    read = run(actions.run(tab, "browser_snapshot", {}))
    assert CODE not in json.dumps(read)
    assert actions.CODE_MASK in read["snapshot"]
    assert actions.CODE_MASK in read["url"]
    for hidden in ("browser_take_screenshot", "browser_evaluate"):
        refused = run(actions.run(tab, hidden, {"function": "() => 1"}))
        assert (refused["ok"], refused["error"]) == (False, "code_on_page")
    # Sent: the box gone (a new page), nothing is masked or refused any more.
    page.boxes, page.url = [], f"https://{HOST}/welcome"
    assert run(actions.run(tab, "browser_take_screenshot", {}))["ok"]
    assert tab.state.code_box is None


def test_any_other_act_drops_a_code_the_agent_did_not_type_first(monkeypatch: Any) -> None:
    page = Page(VERIFY, GOOD)
    tab = tool_tab("apply_agent", page, monkeypatch)
    run(actions.run(tab, "browser_snapshot", {}))
    assert tab.state.code == CODE  # a read keeps it
    run(actions.run(tab, "browser_press_key", {"key": "Tab"}))
    assert tab.state.code is None
    assert run(actions.run(tab, "browser_type_code", {"ref": "e5"}))["error"] == "no_code"


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
