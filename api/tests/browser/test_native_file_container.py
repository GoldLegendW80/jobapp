"""Current native FileList evidence through the real lease API and pinned Chromium."""

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import Stack, no_lease_left, stack

__all__ = ["no_lease_left", "stack"]
pytestmark = pytest.mark.docker


def test_native_file_selection_without_an_echo_tracks_replace_clear_and_rejection(
    stack: Stack,
) -> None:
    lease = Lease(stack, "apply_fill")
    assert lease.put("cv.pdf")["status"] == 200
    assert lease.put("other.pdf")["status"] == 200
    assert lease.tool("browser_navigate", url="http://pages:8080/b5/native-files.html")["ok"]

    def read():
        shot = lease.tool("browser_snapshot")
        assert shot["ok"]
        return shot, ref(shot["snapshot"], "button", "Resume")

    shot, resume = read()
    assert shot["native_files"][resume] == []
    for selected in (["cv.pdf"], ["other.pdf"], ["cv.pdf", "other.pdf"]):
        assert lease.tool("browser_file_upload", ref=resume, paths=selected)["ok"]
        shot, resume = read()
        assert shot["native_files"][resume] == selected
    old = resume
    assert lease.tool("browser_click", ref=ref(shot["snapshot"], "button", "Reset upload"))["ok"]
    shot, resume = read()
    assert old not in shot["native_files"]
    assert shot["native_files"][resume] == []
    assert lease.tool(
        "browser_check", ref=ref(shot["snapshot"], "checkbox", "Reject uploads"), checked=True
    )["ok"]
    assert lease.tool("browser_file_upload", ref=resume, paths=["cv.pdf"])["ok"]
    shot, resume = read()
    assert shot["native_files"][resume] == []
    assert "This file type is not supported." in shot["snapshot"]
    assert (
        lease.tool("browser_file_upload", ref=resume, paths=["foreign.pdf"])["error"]
        == "file_not_given"
    )
    denied = lease.raw(
        "POST",
        f"/leases/{lease.id}/tools/browser_snapshot",
        "{}",
        **{
            "X-Lease-Token": "foreign-lease-token",
            "Content-Type": "application/json",
        },
    )
    assert denied["status"] == 403
    assert "native_files" not in str(denied["body"])
