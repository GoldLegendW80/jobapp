"""Opt-in: the credential check against the real Anthropic API.

Skipped unless JOBAPP_LIVE_ANTHROPIC_API_KEY holds a real API key; CI never sets it, so CI makes
no outbound call. Run by hand:

    JOBAPP_LIVE_ANTHROPIC_API_KEY=sk-ant-api03-... \
        uv run --native-tls pytest tests/agents/test_live.py

It lists one model (no tokens spent), then checks that a made up key is refused as invalid.
"""

import os

import pytest

from app.agents.models import CredentialKind
from app.agents.verify import Outcome, Verifier

LIVE_KEY = os.environ.get("JOBAPP_LIVE_ANTHROPIC_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY, reason="set JOBAPP_LIVE_ANTHROPIC_API_KEY to call the real Anthropic API"
)


def test_real_key_is_verified() -> None:
    assert Verifier().check(CredentialKind.anthropic_api_key, LIVE_KEY).outcome is Outcome.verified


def test_made_up_key_is_invalid() -> None:
    fake = "sk-ant-api03-" + "x" * 95
    assert Verifier().check(CredentialKind.anthropic_api_key, fake).outcome is Outcome.invalid
