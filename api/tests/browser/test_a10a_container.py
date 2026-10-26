"""A real Chromium tab stays available for the person during a private Apply wait."""

import json

import pytest

from tests.browser.test_a9_container import ManualLease
from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import Stack, stack

__all__ = ["stack"]
pytestmark = [pytest.mark.docker]


def test_captcha_is_solved_on_the_same_tab_then_only_guarded_automation_resumes(stack: Stack):
    lease = Lease(stack, "apply_fill")
    path = f"/leases/{lease.id}/apply-wait"

    def wait(action, **body):
        answer = lease.raw("POST", f"{path}/{action}", json.dumps(body))
        assert answer["status"] == 200, answer
        return answer["body"]["lease"]

    try:
        assert lease.tool("browser_navigate", url="http://pages:8080/a10a/wait.html")["ok"]
        name = ref(lease.snapshot(), "textbox", "First name")
        assert lease.tool("browser_type", ref=name, text="Kept on this tab")["ok"]
        before = lease.snapshot()
        assert "I'm not a robot" in before
        started = wait("begin", reason="captcha")
        assert started["state"] == "waiting_person"
        assert started["wait_reason"] == "captcha"
        assert 0 < started["wait_left_s"] <= 1800
        name = ref(before, "textbox", "First name")
        refused = lease.raw(
            "POST",
            f"/leases/{lease.id}/tools/browser_type",
            json.dumps({"ref": name, "text": "Synthetic"}),
        )
        assert refused["status"] == 409
        assert refused["body"]["error"] == "waiting_person"
        watched = lease.tool("browser_snapshot", passive=True)
        assert watched["ok"]
        assert "I'm not a robot" in watched["snapshot"]
        ManualLease.person_clicks(lease, "#captcha")
        solved = lease.tool("browser_snapshot", passive=True)
        assert "I'm not a robot" not in solved["snapshot"]
        reread = wait("begin", reason="captcha")
        assert reread["wait_left_s"] <= started["wait_left_s"]
        assert reread["active_s"] == started["active_s"]
        resumed = wait("resume")
        assert resumed["state"] == "active"
        resumed_page = lease.snapshot()
        assert "Kept on this tab" in resumed_page
        name = ref(resumed_page, "textbox", "First name")
        assert lease.tool("browser_type", ref=name, text="Synthetic")["ok"]
        page = lease.snapshot()
        assert "Synthetic" in page
        assert "Not submitted" in page
        assert (
            lease.tool("browser_click", ref=ref(page, "button", "Submit application"))["error"]
            == "submit"
        )
    finally:
        lease.end()
