"""browserd's lease of Review first's apply agent (A20, purpose `apply_review`): the agent's own
acts never send the form (a wizard's step button aside), a call with the capability is the gate's
browser_click only, a password box never passes, and the lease of One click Apply and every
other purpose stay as they were."""

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

from browserd import config, detect  # noqa: E402
from browserd.tools import actions, routes  # noqa: E402
from tests.browser.test_a19_agent_lease import FIELD, SUBMIT, tab  # noqa: E402


def step(name: str, **rest: Any) -> dict[str, Any]:
    return {"names": [name], "tag": "button", "type": "submit", "in_form": True, **rest}


@pytest.mark.parametrize(
    ("facts", "passes"),
    [
        # A wizard's step, in the languages of the app's people: passes without the capability.
        (step("Next"), True),
        (step("next"), True),
        (step("NEXT"), True),
        (step("  Next  "), True),
        (step("Next step"), True),
        (step("Next →"), True),
        (step("Continue"), True),
        (step("Save and continue"), True),
        (step("Save & Continue"), True),
        (step("Review"), True),
        (step("Review application"), True),
        (step("Suivant"), True),
        (step("Étape suivante"), True),
        (step("Continuer"), True),
        (step("Weiter"), True),
        (step("Nächster Schritt"), True),
        (step("Siguiente"), True),
        (step("Avanti"), True),
        (step("Volgende"), True),
        ({**step("Next"), "names": ["Next", "Next"]}, True),
        # Anything else that submits stays the gate's: fail closed.
        (step("Submit application"), False),
        (step("Submit"), False),
        (step("Apply"), False),
        (step("Send"), False),
        (step("Envoyer ma candidature"), False),
        (step("Bewerbung absenden"), False),
        (step("Finish"), False),
        (step("Done"), False),
        (step("Terminer"), False),
        (step("Continue and submit"), False),
        (step("Next and send"), False),
        (step("Submit and continue"), False),
        (step("Continue to submit"), False),
        (step("Review and submit"), False),
        (step("Nextsubmit"), False),
        (step(""), False),
        ({**step("Next"), "names": ["Next", "Submit application"]}, False),
        ({**step("Next"), "names": []}, False),
        ({**step("Next"), "names": None}, False),
        ({**step("Next"), "selector_hit": True}, False),
        ({**step("Next"), "selector_error": True}, False),
        ({**step("Next"), "tag": "a"}, False),
        ({**step("Next"), "role": "link"}, False),
        ({**step("Next"), "point": SUBMIT}, False),  # the click lands on the send
        ({**step("Next"), "point": None}, False),
        ({**step("Next"), "point": step("Next")}, True),
        (None, False),
    ],
)
def test_a_wizard_step_passes_and_nothing_else_that_submits(facts: Any, passes: bool) -> None:
    assert detect.review_step(facts, action="click") is passes
    refused = tab("apply_review", gate=False).judge(facts, "click", "browser_click")
    # What does not submit at all (a link) passes on every agent lease, as on One click Apply's.
    sends = detect.agent_verdict(facts, action="click") is not None
    assert (refused is None) is (passes or not sends)
    if sends and not passes:
        assert refused["error"] in ("submit_permit", "unreadable")
    # One click Apply's lease is unchanged: a step is a submit there, sent with the capability.
    if passes:
        assert tab("apply_agent", gate=False).judge(facts, "click", "browser_click") is not None


@pytest.mark.parametrize("action", ["key", "check", "type"])
def test_a_step_word_passes_only_as_a_click(action: str) -> None:
    assert not detect.review_step(step("Next"), action=action)


def test_the_send_is_refused_to_the_agent_and_the_gate_s_click_passes() -> None:
    refused = tab("apply_review", gate=False).judge(SUBMIT, "click", "browser_click")
    assert refused is not None and refused["error"] == "submit_permit"
    gate = tab("apply_review", gate=True)
    assert gate.judge(SUBMIT, "click", "browser_click") is None
    assert gate.submitted
    secret = {**FIELD, "secret": True}
    assert tab("apply_review", gate=True).judge(secret, "type", "browser_type")["error"] == (
        "credential"
    )
    assert tab("apply_review", gate=False).judge(FIELD, "type", "browser_type") is None


def test_on_the_review_lease_the_capability_opens_a_click_only() -> None:
    assert "apply_review" in routes.GATE_PURPOSES
    assert config.LEASE_MAX_S["apply_review"] == 3600
    assert tab("apply_review", gate=False).agent and tab("apply_review", gate=False).review
    assert not tab("apply_agent", gate=False).review
    # routes.call_tool: `agent` (AGENT_ACTS with the capability) is One click Apply's lease only.
    source = Path(routes.__file__).read_text()
    assert "agent = lease.request.purpose == actions.AGENT_PURPOSE" in source
    assert actions.REVIEW_PURPOSE == "apply_review"


def test_the_api_and_browserd_know_the_same_purposes() -> None:
    from app.browser import leases

    assert set(leases.LEASE_MAX_S) == set(config.LEASE_MAX_S)
    assert leases.TIERS["apply_review"] == leases.TIERS["apply_agent"]
    lease = SimpleNamespace(request=SimpleNamespace(purpose="apply_review"), gate_hash=b"")
    assert routes.issue_capability(lease) and lease.gate_hash
