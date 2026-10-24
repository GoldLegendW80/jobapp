"""Only the two additional platforms have a bounded, strict consent map."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.schemas import SearchSection

KINDS = ("indeed", "welcome_to_the_jungle")
ACCEPTED = {
    "enabled": True,
    "accepted_at": "2026-10-08T09:30:00+00:00",
    "text_version": 1,
}


@pytest.mark.parametrize("kind", KINDS)
def test_consent_map_has_no_legacy_source_or_interactive_selection(kind: str) -> None:
    empty = SearchSection.model_validate({}).model_dump(mode="json")
    assert empty["platform_consents"] == {}
    saved = SearchSection.model_validate({"platform_consents": {kind: ACCEPTED}})
    assert saved.model_dump(mode="json")["platform_consents"] == {kind: ACCEPTED}
    assert saved.browser_sources == []
    assert saved.interactive_sites == []


@pytest.mark.parametrize("kind", ["linkedin", "freework", "malt", "Indeed", "unknown", ""])
def test_consent_map_rejects_other_authorities(kind: str) -> None:
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"platform_consents": {kind: ACCEPTED}})


@pytest.mark.parametrize("value", [None, [], "indeed", 1, True])
def test_consent_map_requires_an_object(value: Any) -> None:
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"platform_consents": value})


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"enabled": None},
        {"accepted_at": None},
        {"accepted_at": "2026-10-08"},
        {"accepted_at": "2026-10-08T09:30:00"},
        {"accepted_at": "not a date"},
        {"accepted_at": "x" * 41},
        {"accepted_at": 1},
        {"text_version": None},
        {"text_version": True},
        {"text_version": "1"},
        {"text_version": 0},
        {"text_version": 1001},
        {"query": "Robotics"},
        {"paused_until": "2030-01-01T00:00:00+00:00"},
    ],
)
def test_consent_entries_reject_malformed_or_extra_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"platform_consents": {"indeed": {**ACCEPTED, **changes}}})
