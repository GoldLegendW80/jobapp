"""A25 on real Chromium in the browser container: the code the person gave in the app, typed by
browserd and sent in one call (`browser_type_code` with the box and the button that sends it),
on the pages of tests/browser_pages/a25/: one box under six slots that show it (sent, kept with
"That code is wrong", or a box hidden in place), six boxes of one character, a box the page sends
by itself, and a GET form whose next page shows it again. The call answers `taken` or `kept`
only; no answer of the lease holds the code (the tools close instead, `code_shown`); a kept box
and its slots are empty; back, forward and a screenshot are refused. Needs an existing
BROWSER_TEST_IMAGE; never builds or pulls one."""

import json
import os
import secrets
import uuid
from typing import Any

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
PAGES = "http://pages:8080/a25"


@pytest.fixture(scope="module")
def a25(tmp_path_factory):
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path_factory.mktemp("a25") / "a25.yml"
    mounted = {"volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"]}
    override.write_text(json.dumps({"services": {"browser": {"pull_policy": "never", **mounted}}}))
    stack = Stack("a25-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        yield stack
    finally:
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


JSON = {"Content-Type": "application/json"}


def control(lease: Lease, path: str, body: dict[str, Any]) -> int:
    sent = lease.raw("POST", f"/leases/{lease.id}/{path}", json.dumps(body), **JSON)
    return sent["status"]


# Each page, the outcome of the code's call, and whether the tools close at that call.
SHAPES = [
    ("slots.html", "taken"),
    ("slots.html?mode=wrong", "kept"),
    ("slots.html?mode=hide", "taken"),
    ("six.html", "taken"),
    ("auto.html", "taken"),
    ("verify.html", "code_shown"),
]


@pytest.mark.parametrize(("page", "outcome"), SHAPES)
def test_the_code_is_typed_sent_and_never_answered_on_real_chromium(
    a25, page: str, outcome: str
) -> None:
    code = "".join(secrets.choice("GHJKMNPQRSTVWXYZ") for _ in range(6))
    lease = Lease(a25, "apply_agent")
    a25.redactions += [lease.token, lease.capability]
    said: list[str] = []

    def seen(name: str, capability: str | None = None, **args: Any) -> dict[str, Any]:
        answer = lease.tool(name, capability, **args)
        said.append(json.dumps(answer))
        assert code.casefold() not in said[-1].casefold(), name
        return answer

    try:
        assert seen("browser_navigate", url=f"{PAGES}/{page}")["ok"]
        ask = secrets.token_hex(8)
        assert control(lease, "ask/begin", {"left_s": 300, "ask": ask, "code": True}) == 200
        assert control(lease, "code", {"user_id": "e2e-b5", "ask": ask, "code": code}) == 200
        assert control(lease, "ask/resume", {}) == 200
        shot = seen("browser_snapshot")["snapshot"]
        name = "Digit 1" if page.startswith("six") else "Verification code"
        args = {"ref": ref(shot, "textbox", name)}
        if not page.startswith("auto"):
            args["submit"] = ref(shot, "button", "Verify")
            # Without the capability the Verify button is refused before the first key, and
            # the code stays for the same call with it (the api reads the emergency stop first).
            assert seen("browser_type_code", **args)["error"] == "submit_permit"
        typed = seen("browser_type_code", lease.capability, **args)
        assert typed.get("code", typed.get("error")) == outcome, typed
        assert set(typed) <= {"ok", "code", "error", "message", "rule", "submit_clicked"}
        after = seen("browser_snapshot")
        if outcome == "code_shown":
            assert after["error"] == "code_shown"
            assert seen("browser_tabs", action="list")["error"] == "code_shown"
            return
        assert after["ok"]
        if outcome == "kept":
            assert "That code is wrong" in after["snapshot"]
            assert 'textbox "Verification code"' in after["snapshot"]
        assert seen("browser_take_screenshot")["error"] == "code_on_page"
        assert seen("browser_navigate_back")["error"] == "code_history"
        assert seen("browser_navigate_forward")["error"] == "code_history"
    finally:
        lease.end()
    assert not any(code.casefold() in text.casefold() for text in said)
