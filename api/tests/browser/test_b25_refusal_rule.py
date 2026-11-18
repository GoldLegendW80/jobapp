"""Browser B25: browserd's refusals name their rule in the answer, as its warning logs it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import actions


def test_a_refusal_names_its_rule_and_a_plain_failure_none() -> None:
    assert actions.refused("host_not_allowed") == {
        "ok": False,
        "error": "host_not_allowed",
        "message": actions.MESSAGES["host_not_allowed"],
        "rule": "host_not_allowed",
    }
    assert actions.refused("submit_permit", "submit")["rule"] == "submit"
    assert "rule" not in actions.failure("navigation_failed", "The page did not load.")
    assert actions.failure("unsupported_discovery", "Refused.", "boundary")["rule"] == "boundary"
