"""A25 on real Chromium in the browser container: the code the person gave in the app, typed by
browserd into the one time code box of a GET form (tests/browser_pages/a25/), is never in an answer
of the lease: masked right after typing, after back then forward (Chromium puts the value back in
a new box), in the address, the text and the title of the page the form's GET submit loads, and
after back from there; a screenshot is refused all along. Needs an existing BROWSER_TEST_IMAGE;
never builds or pulls one."""

import json
import os
import secrets
import uuid
from typing import Any

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
VERIFY = "http://pages:8080/a25/verify.html"
MASK = "[the code the person gave]"


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


def control(lease: Lease, path: str, body: dict[str, Any]) -> int:
    sent = lease.raw("POST", f"/leases/{lease.id}/{path}", json.dumps(body), **JSON)
    return sent["status"]


JSON = {"Content-Type": "application/json"}


def test_the_code_stays_masked_on_real_chromium(a25) -> None:
    code = f"Q{secrets.randbelow(10**10):010d}"
    lease = Lease(a25, "apply_agent")
    a25.redactions += [lease.token, lease.capability]

    def seen(name: str, capability: str | None = None, **args: Any) -> dict[str, Any]:
        answer = lease.tool(name, capability, **args)
        text = json.dumps(answer)
        assert code not in text, name
        assert code.lower() not in text, name
        return answer

    try:
        assert seen("browser_navigate", url=VERIFY)["ok"]
        ask = secrets.token_hex(8)
        assert control(lease, "ask/begin", {"left_s": 300, "ask": ask, "code": True}) == 200
        given = {"user_id": "e2e-b5", "ask": ask, "code": code}
        assert control(lease, "code", given) == 200
        assert control(lease, "ask/resume", {}) == 200
        box = ref(seen("browser_snapshot")["snapshot"], "textbox", "Verification code")
        assert seen("browser_type_code", ref=box).get("code_typed") is True
        assert MASK in seen("browser_snapshot")["snapshot"]
        assert seen("browser_take_screenshot")["error"] == "code_on_page"
        # Back, then forward: Chromium restores the value in a new box.
        assert seen("browser_navigate_back")["ok"]
        assert seen("browser_navigate_forward")["ok"]
        page = seen("browser_snapshot")["snapshot"]
        assert MASK in page
        # The form's GET submit (the gate's click): the code in the next address, text and title.
        sent = seen("browser_click", lease.capability, ref=ref(page, "button", "Verify"))
        assert MASK in sent["url"]
        after = seen("browser_snapshot")
        assert MASK in after["url"]
        assert f"You entered {MASK}" in after["snapshot"]
        assert MASK in after["title"]
        assert seen("browser_navigate_back")["ok"]
        assert MASK in seen("browser_snapshot")["snapshot"]
        assert seen("browser_take_screenshot")["error"] == "code_on_page"
    finally:
        lease.end()
