"""The private opener on real Chromium, local fixtures and counted actions only."""

import json

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import no_lease_left, stack

__all__ = ["no_lease_left", "stack"]
pytestmark = [pytest.mark.docker]
URL = "http://pages:8080/linkedin/jobs/view/4100000001/"


def open_modal(lease, job="4100000001", **headers):
    return lease.raw(
        "POST", f"/leases/{lease.id}/linkedin/open", json.dumps({"job_id": job}), **headers
    )


def test_chromium_private_opener_opens_once_model_clicks_keep_refusing(stack):
    lease = Lease(stack, "apply_submit")
    try:
        assert lease.tool("browser_navigate", url=URL)["ok"]
        before = lease.snapshot()
        assert (
            lease.tool("browser_click", ref=ref(before, "button", "Easy Apply"))["error"]
            == "submit"
        )
        assert "Opened 0, submitted 0" in lease.snapshot()
        assert open_modal(lease, **{"X-Lease-Token": "wrong"})["status"] == 403
        assert open_modal(lease, job="2")["body"]["ok"] is False
        assert open_modal(lease)["body"]["ok"] is True
        current = lease.snapshot()
        assert "Opened 1, submitted 0" in current
        assert open_modal(lease)["body"]["ok"] is False
        final = ref(lease.snapshot(), "button", "Submit application")
        assert lease.tool("browser_click", ref=final)["error"] == "submit"
        assert lease.tool("browser_click", capability=lease.capability, ref=final)["ok"]
        assert "Opened 1, submitted 1" in lease.snapshot()
    finally:
        lease.end()


@pytest.mark.parametrize(
    "variant",
    [
        "form",
        "associated",
        "submit",
        "nested",
        "final",
        "duplicate",
        "iframe",
        "overlay",
        "job-change",
        "replacement",
    ],
)
def test_chromium_refuses_unsafe_or_changed_opener(stack, variant):
    lease = Lease(stack, "apply_fill")
    try:
        assert lease.tool("browser_navigate", url=URL + "?variant=" + variant)["ok"]
        assert open_modal(lease)["body"]["ok"] is False
        assert "Opened 0, submitted 0" in lease.snapshot()
    finally:
        lease.end()
