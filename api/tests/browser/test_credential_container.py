"""browserd's read of credential inputs through the real lease API and pinned Chromium: the
snapshot's `credentials`, the refusal to type into one, and the api reading a two step login's
first page as a sign in wall (tests/browser_pages/b5/credentials.html)."""

import pytest

from app.apply.ats import base
from tests.browser.test_b5_container import Lease
from tests.browser.test_browser_container import Stack, no_lease_left, stack

__all__ = ["no_lease_left", "stack"]
pytestmark = pytest.mark.docker

PAGE = "http://pages:8080/b5/credentials.html"
READ = {
    "Email": True,  # a username box of a form that holds a hidden password box
    "Secret word": True,  # a password box
    "Enter the code": True,  # one-time-code
    "Shown password": True,  # new-password, shown as text
    "Passkey": True,  # webauthn
    "Contact email": False,
    "GitHub username": False,  # a username box of a form that holds none
    "Password hint": False,  # a label is the api's signal, not browserd's
    "Country": False,
}


def entries(shot: dict) -> dict[str, str]:
    roles = ("textbox", "combobox")
    return {n.name: n.ref for n in base.parse_snapshot(shot["snapshot"]).walk() if n.role in roles}


def test_the_snapshot_reads_each_credential_input_and_the_tools_refuse_to_type_into_one(
    stack: Stack,
) -> None:
    lease = Lease(stack, "apply_fill")
    assert lease.tool("browser_navigate", url=PAGE)["ok"]
    shot = lease.tool("browser_snapshot")
    assert shot["ok"], shot
    refs = entries(shot)
    assert {name: shot["credentials"].get(ref) for name, ref in refs.items()} == READ
    for name, credential in READ.items():
        if name == "Country":
            continue
        typed = lease.tool("browser_type", ref=refs[name], text="e2e")
        assert typed["ok"] is not credential, (name, typed)
        if credential:
            assert typed["error"] == "credential"


@pytest.mark.parametrize(("part", "step"), [("login", "signin"), ("application", "")])
def test_a_two_step_logins_first_page_is_a_wall_and_an_application_is_not(
    stack: Stack, part: str, step: str
) -> None:
    lease = Lease(stack, "apply_fill")
    assert lease.tool("browser_navigate", url=f"{PAGE}#{part}")["ok"]
    shot = lease.tool("browser_snapshot")
    assert shot["ok"], shot
    assert base.Adapter().pending_of(shot) == step
