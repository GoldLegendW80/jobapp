"""Real Chromium inputs through actual tool routes, Manager and Live ownership.

The sandboxed browser is owned by the normal isolated browser fixture. A second Manager uses
that attached browser with no-op lifecycle methods; all route, lease, capability, live owner,
pacing and native input code is real. Only the pacer's sleep is injected to place a takeover
at the exact awaited boundary, so no timing race or altered production pacing is needed.
"""

import json
import uuid

import pytest

from tests.browser.test_browser_container import Stack, no_lease_left, stack

__all__ = ["no_lease_left", "stack"]
pytestmark = [pytest.mark.docker]

NATIVE = r"""
import asyncio, json, uuid
from types import SimpleNamespace
from playwright.async_api import async_playwright
from browserd.app import Service, HttpRequest
from browserd.config import Config
from browserd.leases import Manager, Request
from browserd.live import Live
from browserd.probe import cdp_port
from browserd.tools import actions, pacing, routes


class Attached:
    def __init__(self, browser):
        self.browser = browser

    async def stop(self):
        pass

    async def kill(self):
        pass


async def main():
    checks = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port()}")
        page = browser.contexts[0].pages[0]
        for purpose in ("apply_fill", "apply_submit", "profile_edit"):
            for during in (False, True):
                await page.goto("http://pages:8080/b5/form.html")
                label = (
                    "Next"
                    if purpose == "apply_fill"
                    else "Save"
                    if purpose == "profile_edit"
                    else "Submit"
                )
                await page.set_content(
                    f'<title>Invented form</title><input aria-label="Name" value="Approved">'
                    f'<form><button type="button" onclick="window.acts++">{label}</button></form>'
                )
                await page.evaluate("window.acts=0")

                async def start(request, lease_id):
                    return Attached(browser)

                manager = Manager(Config(secret=b"e2e-live-native"), start)
                lease, token = await manager.acquire(
                    Request(
                        "e2e-owner",
                        str(uuid.uuid4()),
                        20000,
                        purpose,
                        True,
                        ("en",),
                        "UTC",
                        hosts=("pages",),
                    )
                )
                svc = Service(manager, b"e2e-live-native")
                svc.live = Live(manager, b"e2e-live-native")

                async def no_sleep(seconds):
                    pass

                routes.PACER = pacing.Pacer(no_sleep)
                state = routes.state_of(lease)
                tab = routes._tab(svc, lease)
                snap = await actions.run(tab, "browser_snapshot", {})
                import re

                ref = re.search(
                    r'button "' + label + r'".*?\[ref=(e\d+)\]', snap["snapshot"]
                ).group(1)
                headers = {"x-lease-token": token}
                if cap := routes.issue_capability(lease):
                    headers["x-gate-capability"] = cap
                req = HttpRequest(
                    "POST", "/", headers, json.dumps({"ref": ref}).encode()
                )

                async def click():
                    return await routes.call_tool(
                        svc, req, {"name": "browser_click", "lease_id": lease.lease_id}
                    )

                if during:

                    async def takeover(seconds):
                        svc.live.take(lease.lease_id, "viewer")
                        svc.live.give_back(lease.lease_id)

                    routes.PACER = pacing.Pacer(takeover)
                    result = (await click())[1]
                    assert result.get("error") == "person_input", result
                else:
                    svc.live.take(lease.lease_id, "viewer")
                    task = asyncio.create_task(click())
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    assert not task.done()
                    await page.locator("input").fill("Person changed")
                    svc.live.give_back(lease.lease_id)
                    result = (await task)[1]
                    assert result.get("error") == "person_used", result
                    assert "Person changed" in result["page"]["snapshot"]
                assert await page.evaluate("window.acts") == 0
                await manager.release(lease.lease_id, "test_done")
                checks += 1
        # The private opener follows the same native boundary, after its structural checks.
        await page.goto("http://pages:8080/jobs/view/123")
        await page.set_content(
            '<title>Invented posting</title>'
            '<button type="button" onclick="window.acts++">Easy Apply</button>'
        )
        await page.evaluate("window.acts=0")
        manager = Manager(
            Config(
                secret=b"e2e-live-native", linkedin_stub_base_url="http://pages:8080"
            ),
            start,
        )
        lease, token = await manager.acquire(
            Request(
                "e2e-owner",
                str(uuid.uuid4()),
                20000,
                "apply_fill",
                True,
                ("en",),
                "UTC",
                hosts=("pages",),
            )
        )
        svc = Service(manager, b"e2e-live-native")
        svc.live = Live(manager, b"e2e-live-native")

        async def takeover(seconds):
            svc.live.take(lease.lease_id, "viewer")
            svc.live.give_back(lease.lease_id)

        routes.PACER = pacing.Pacer(takeover)
        req = HttpRequest("POST", "/", {"x-lease-token": token}, b'{"job_id":"123"}')
        result = (await routes.linkedin_open(svc, req, {"lease_id": lease.lease_id}))[1]
        assert result.get("error") == "person_input", result
        assert await page.evaluate("window.acts") == 0
        await manager.release(lease.lease_id, "test_done")
        checks += 1
    print(json.dumps({"checks": checks, "native_clicks": 0}))


asyncio.run(main())
"""


def test_live_takeover_owns_native_fill_submit_save_and_private_opener(stack: Stack):
    stack.lease(str(uuid.uuid4()), 20000)
    result = json.loads(stack.python(NATIVE))
    assert result == {"checks": 7, "native_clicks": 0}
