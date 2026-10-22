"""Apply myself (apply A9) on the real browser container: an `apply_manual` lease on the test form
of tests/browser_pages/b5/. The person's click comes from the X test extension on the display,
as a click of the live view does; where the button stands is read over the lease's local CDP.

What runs: the file chooser the person opens is intercepted (no dialog window on the display),
the passive watch sees it, and the document of the lease the panel names answers it; outside the
app's turn the app's keys and clicks are refused (`person_input`), during it they reach the page,
and a Take over ends the turn at once. Marked `docker`, with the stack and the image of
test_browser_container.py (built under a tag of its own unless BROWSER_TEST_IMAGE names one).
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.browser.test_b5_container import FORM, PDF_OF, Lease, ref
from tests.browser.test_b7_container import window_titles
from tests.browser.test_browser_container import Stack, stack

__all__ = ["stack"]

pytestmark = [pytest.mark.docker]

WHERE = """
import asyncio, json, sys
from browserd import probe
from playwright.async_api import async_playwright

async def main(selector):
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{probe.cdp_port()}")
        page = b.contexts[0].pages[0]
        print(json.dumps(await page.locator(selector).evaluate(
            '''(el) => {
                el.scrollIntoView({ block: "center" });
                const r = el.getBoundingClientRect();
                return [Math.round(window.screenX + r.left + r.width / 2),
                        Math.round(window.screenY + window.outerHeight - window.innerHeight
                                   + r.top + r.height / 2)];
            }''')))

asyncio.run(main(sys.argv[1]))
"""

CLICK = """
import ctypes, sys
x, t = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
t.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 3 + [ctypes.c_ulong]
t.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
d = x.XOpenDisplay(b":1")
t.XTestFakeMotionEvent(d, 0, int(sys.argv[1]), int(sys.argv[2]), 0)
t.XTestFakeButtonEvent(d, 1, 1, 0)
t.XTestFakeButtonEvent(d, 1, 0, 0)
x.XSync(d, 0)
"""


class ManualLease(Lease):
    """A lease of Apply myself: the B5 helper's calls on a lease made with that purpose."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        profile = str(uuid.uuid4())
        body = {
            "user_id": "e2e-a9",
            "profile_id": profile,
            "uid": 20000 + uuid.UUID(profile).int % 30000,
            "purpose": "apply_manual",
            "automation": True,
            "hosts": ["pages"],
            "submit_selectors": [],
        }
        answer = stack.call("POST", "/leases", body)
        assert answer["status"] == 201, answer
        self.id = answer["body"]["lease_id"]
        self.token = answer["body"]["lease_token"]
        self.capability = None

    def turn(self, viewer: str | None) -> dict[str, Any]:
        """The api's side of the live input: null starts the app's turn, a viewer ends it."""
        answer = self.stack.call("POST", f"/leases/{self.id}/live/input", {"viewer": viewer})
        assert answer["status"] == 200, answer
        return answer["body"]

    def person_clicks(self, selector: str) -> None:
        spot = json.loads(self.stack.python(WHERE, selector).strip().splitlines()[-1])
        self.stack.execute("browser", "python", "-c", CLICK, *map(str, spot), user="1000")

    def watch(self) -> dict[str, Any]:
        result = self.tool("browser_snapshot", passive=True)
        assert result["ok"], result
        return result


def test_a_chooser_the_person_opens_is_answered_with_the_document_named(stack: Stack) -> None:
    lease = ManualLease(stack)
    try:
        assert lease.put("cv.pdf")["status"] == 200
        assert lease.put("letter.pdf")["status"] == 200
        assert lease.tool("browser_navigate", url=FORM)["ok"]
        assert lease.watch().get("chooser") is None
        lease.turn(None)
        nothing = lease.tool("browser_file_upload", paths=["letter.pdf"])
        assert nothing["error"] == "no_chooser"
        lease.turn(str(uuid.uuid4()))

        lease.person_clicks("#attach")
        for _ in range(60):  # each look is a docker exec round trip, no sleep
            if (watched := lease.watch()).get("chooser"):
                break
        else:
            raise AssertionError("the watch never saw the file chooser")
        assert "Open File" not in window_titles(stack)  # intercepted: no dialog over a folder

        lease.turn(None)
        done = lease.tool(
            "browser_file_upload", paths=["letter.pdf"], chooser_id=watched["chooser_id"]
        )
        assert done["ok"], done
        assert done["uploaded"] == ["letter.pdf"]
        assert f"letter.pdf:{len(PDF_OF)}" in lease.snapshot()
        assert lease.watch().get("chooser") is None
        again = lease.tool("browser_file_upload", paths=["letter.pdf"])
        assert again["error"] == "no_chooser"
    finally:
        lease.end()


def test_the_app_types_only_during_its_turn_and_a_take_over_ends_it(stack: Stack) -> None:
    lease = ManualLease(stack)
    try:
        assert lease.tool("browser_navigate", url=FORM)["ok"]
        page = lease.snapshot()
        name = ref(page, "textbox", "First name")
        for tool, args in (
            ("browser_type", {"ref": name, "text": "Adaline"}),
            ("browser_click", {"ref": ref(page, "button", "Submit application")}),
            ("browser_press_key", {"key": "Enter"}),
        ):
            assert lease.tool(tool, **args)["error"] == "person_input", tool

        assert lease.turn(None) == {"input": "agent"}  # "Fill my details"
        assert lease.tool("browser_type", ref=name, text="Adaline")["ok"]
        # Even in its turn, the app's click on Submit is refused by the tool layer (B5).
        submit = lease.tool("browser_click", ref=ref(page, "button", "Submit application"))
        assert submit["error"] == "submit"

        assert lease.turn(str(uuid.uuid4())) == {"input": "person"}  # Take over
        refused = lease.tool("browser_type", ref=name, text="x")
        assert refused["error"] == "person_input"
        after = lease.snapshot()
        assert 'textbox "First name"' in after
        assert ": Adaline" in after
        assert "Not submitted" in after
    finally:
        lease.end()


def test_the_chooser_the_apps_attach_click_opens_is_not_kept_for_the_next_document(
    stack: Stack,
) -> None:
    lease = ManualLease(stack)
    letter = "%PDF-1.7\n" + "1" * 999
    try:
        assert lease.put("cv.pdf")["status"] == 200
        assert lease.put("letter.pdf", letter)["status"] == 200
        assert lease.tool("browser_navigate", url=FORM)["ok"]
        attach = ref(lease.snapshot(), "button", "Attach cover letter")
        lease.turn(None)
        done = lease.tool("browser_file_upload", ref=attach, paths=["letter.pdf"])
        assert done["ok"], done
        assert "chooser" not in done
        assert lease.watch().get("chooser") is None
        stale = lease.tool("browser_file_upload", paths=["cv.pdf"])
        assert stale["error"] == "no_chooser"
        page = lease.snapshot()
        assert f"letter.pdf:{len(letter)}" in page
        assert "cv.pdf" not in page
    finally:
        lease.end()


def test_an_already_open_chooser_is_not_consumed_by_an_app_attach_button(stack: Stack) -> None:
    lease = ManualLease(stack)
    try:
        assert lease.put("cv.pdf")["status"] == 200
        assert lease.put("letter.pdf")["status"] == 200
        assert lease.tool("browser_navigate", url=FORM)["ok"]
        attach = ref(lease.snapshot(), "button", "Attach cover letter")
        lease.person_clicks("#attach")
        for _ in range(60):
            if (before := lease.watch()).get("chooser"):
                break
        else:
            raise AssertionError("the person's chooser never opened")
        lease.turn(None)
        rejected = lease.tool("browser_file_upload", ref=attach, paths=["cv.pdf"])
        assert rejected["error"] == "chooser_changed"
        assert lease.watch()["chooser_id"] == before["chooser_id"]
        accepted = lease.tool(
            "browser_file_upload", paths=["letter.pdf"], chooser_id=before["chooser_id"]
        )
        assert accepted["ok"]
        assert "cv.pdf" not in lease.snapshot()
        assert f"letter.pdf:{len(PDF_OF)}" in lease.snapshot()
    finally:
        lease.end()


def test_take_over_stops_real_chromium_typing_already_in_progress(stack: Stack) -> None:
    lease = ManualLease(stack)
    value = "A" * 199  # Below PASTE_FROM: exercise the paced per-character input checks.
    read = """
import asyncio
from browserd import probe
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{probe.cdp_port()}")
        print(await browser.contexts[0].pages[0].locator('#first_name').input_value())
asyncio.run(main())
"""
    try:
        assert lease.tool("browser_navigate", url=FORM)["ok"]
        name = ref(lease.snapshot(), "textbox", "First name")
        lease.turn(None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            typing = pool.submit(lease.tool, "browser_type", ref=name, text=value)
            for _ in range(60):
                current = stack.python(read).strip()
                if current:
                    break
            else:
                raise AssertionError("typing never reached the native input")
            assert len(current) < len(value)
            lease.turn(str(uuid.uuid4()))
            assert typing.result(timeout=120)["error"] == "person_input"
        stopped = stack.python(read).strip()
        assert stopped.startswith(current)
        assert len(stopped) < len(value)
        assert stack.python(read).strip() == stopped
        assert "Not submitted" in lease.snapshot()
    finally:
        lease.end()
