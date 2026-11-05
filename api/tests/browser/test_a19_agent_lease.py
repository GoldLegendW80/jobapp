"""browserd's lease of One click Apply's agent (A19, purpose `apply_agent`): a submit element only
with the lease's capability (which the api sends while the emergency stop is off), never a
password or one time code box, no host list, and every other purpose unchanged."""

from __future__ import annotations

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

from browserd import detect  # noqa: E402
from browserd.tools import actions, pacing, routes  # noqa: E402

FIELD = {"names": ["Email"], "tag": "input", "type": "email", "credential": False, "secret": False}
SUBMIT = {"names": ["Submit application"], "tag": "button", "type": "submit"}
NEXT = {"names": ["Next"], "tag": "button", "type": "submit", "in_form": True}
DELETE = {"names": ["Delete my account"], "tag": "button", "type": "button"}


@pytest.mark.parametrize(
    ("facts", "action", "code"),
    [
        (FIELD, "type", None),
        ({**FIELD, "type": "password", "secret": True}, "type", "credential"),
        ({**FIELD, "secret": None}, "type", "credential"),  # unread: fail closed
        (SUBMIT, "click", "submit"),
        (NEXT, "click", "submit"),
        ({**FIELD, "in_form": True}, "key", "submit"),  # Enter in a field submits its form
        (DELETE, "click", None),  # no danger words on this lease (the owner's "no guardrails")
        ({"names": ["Apply now"], "tag": "a", "type": ""}, "click", None),
        (None, "click", "unreadable"),
        ({**DELETE, "point": SUBMIT}, "click", "submit"),  # the click lands on a submit
        # A typing is refused `credential` whenever its facts or its click point cannot be
        # read (hard rule 7 fails closed: a typing never needs the capability), and a click
        # point outside the field is judged as a click there (round 1 of the review).
        (None, "type", "credential"),
        ({"names": "Email", "tag": "input"}, "type", "credential"),
        ({**FIELD, "point": None}, "type", "credential"),
        ({**FIELD, "point": {"names": None}}, "type", "credential"),
        ({**FIELD, "point": {**FIELD, "inside": True}}, "type", None),
        ({**FIELD, "point": {**FIELD, "secret": True, "inside": True}}, "type", "credential"),
        ({**FIELD, "point": {**FIELD, "inside": False}}, "type", None),
        ({**FIELD, "point": {**SUBMIT, "secret": False, "inside": False}}, "type", "submit"),
        ({**FIELD, "point": {**NEXT, "secret": False}}, "type", "submit"),  # no `inside`: outside
        ({**FIELD, "point": {**FIELD, "secret": None, "inside": False}}, "type", "credential"),
    ],
)
def test_the_agent_verdict(facts: Any, action: str, code: str | None) -> None:
    assert detect.agent_verdict(facts, action=action) == code


def button(name: str, tag: str = "button", kind: str = "button") -> dict[str, Any]:
    return {"names": [name], "tag": tag, "type": kind, "role": "", "in_form": False}


@pytest.mark.parametrize(
    ("name", "marked"),
    [
        # Sends in German and French, and a wizard's last word alone (a type=button the page's
        # script sends with): marked, so the emergency stop is read and `submitting` committed.
        ("Bewerbung absenden", True),
        ("Absenden", True),
        ("Jetzt absenden", True),
        ("Abschicken", True),
        ("Bewerbung einreichen", True),
        ("Einreichen", True),
        ("Bewerbung abschließen", True),
        ("Senden", True),
        ("Envoyer ma candidature", True),
        ("Envoyer", True),
        ("Valider ma candidature", True),
        ("Terminer ma candidature", True),
        ("Finish", True),
        ("Finish application", True),
        ("Done", True),
        ("DONE", True),
        ("Terminer", True),
        ("Terminez", True),
        ("Fertig", True),
        ("Submit", True),
        ("Postuler", True),
        ("Apply", True),
        ("Jetzt bewerben", False),  # an opener in German: declared open
        # A step, a page or a control that sends nothing: unmarked.
        ("Next", False),
        ("Weiter", False),
        ("Suivant", False),
        ("Continue", False),
        ("Zurück", False),
        ("Abbrechen", False),
        ("Abschnitt anzeigen", False),
        ("Einreise", False),
        ("Undone changes", False),
        ("Termine", False),
        ("Show more", False),
        ("Upload", False),
        ("Fertigkeiten", False),
        ("", False),
    ],
)
def test_the_words_that_mark_a_send_on_the_agent_lease(name: str, marked: bool) -> None:
    code = detect.agent_verdict(button(name), action="click")
    assert code == ("submit" if marked else None)
    # A link opens a page: its words never mark it.
    assert detect.agent_verdict(button(name, tag="a", kind=""), action="click") is None


def test_the_send_words_of_the_agent_lease_change_no_other_lease() -> None:
    for name in ("Bewerbung absenden", "Done", "Fertig"):
        assert not detect.says_submit([name])


@pytest.mark.parametrize("facts", [None, {**FIELD, "point": None}, {**FIELD, "secret": None}])
def test_an_unreadable_typing_is_refused_with_the_capability_too(facts: Any) -> None:
    for gate in (False, True):
        refused = tab("apply_agent", gate=gate).judge(facts, "type", "browser_type")
        assert refused is not None
        assert refused["error"] == "credential"


def tab(purpose: str, gate: bool) -> actions.Tab:
    return actions.Tab(
        request=SimpleNamespace(purpose=purpose, hosts=(), submit_selectors=()),
        state=actions.State(),
        browser=None,
        pacer=pacing.Pacer(),
        gate=gate,
    )


def test_a_submit_needs_the_capability_and_a_password_never_passes() -> None:
    refused = tab("apply_agent", gate=False).judge(SUBMIT, "click", "browser_click")
    assert refused is not None
    assert refused["error"] == "submit_permit"
    permitted = tab("apply_agent", gate=True)
    assert permitted.judge(SUBMIT, "click", "browser_click") is None
    assert permitted.submitted
    secret = {**FIELD, "secret": True}
    assert tab("apply_agent", gate=True).judge(secret, "type", "browser_type")["error"] == (
        "credential"
    )


def test_any_public_address_and_no_other_purpose_changes() -> None:
    agent = tab("apply_agent", gate=False)
    assert agent.allowed("https://careers.example.org/jobs/1")
    assert not agent.allowed("file:///etc/passwd")
    assert not tab("apply_fill", gate=False).allowed("https://careers.example.org/jobs/1")
    # Review first's fill keeps the submit detector and its danger words.
    assert tab("apply_fill", gate=False).judge(DELETE, "click", "browser_click")["error"] == (
        "danger"
    )
    assert tab("apply_fill", gate=False).judge(SUBMIT, "click", "browser_click")["error"] == (
        "submit"
    )


def test_the_capability_is_issued_to_the_agent_lease() -> None:
    assert "apply_agent" in routes.GATE_PURPOSES
    assert {"browser_click", "browser_type", "browser_press_key"} <= actions.AGENT_ACTS
    lease = SimpleNamespace(request=SimpleNamespace(purpose="apply_agent"), gate_hash=b"")
    assert routes.issue_capability(lease)
    assert lease.gate_hash
