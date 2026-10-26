"""Execution intent is a strict protected-source field, separate from consent."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.browser.linkedin_router import LinkedInIn
from app.opportunities.platforms_router import PlatformIn
from app.profile.schemas import BrowserSource, SearchSection


@pytest.mark.parametrize("kind", ["linkedin", "freework", "site", "vie", "google_careers"])
def test_old_source_rows_default_to_legacy_intent(kind: str) -> None:
    source = BrowserSource.model_validate({"kind": kind, "query": "Robotics"})
    assert source.interactive_only is False
    assert source.model_dump(mode="json")["interactive_only"] is False


@pytest.mark.parametrize("kind", ["linkedin", "freework"])
@pytest.mark.parametrize("flag", [False, True])
def test_protected_intent_does_not_grant_permission(kind: str, flag: bool) -> None:
    search = SearchSection.model_validate(
        {"browser_sources": [{"kind": kind, "query": "Robotics", "interactive_only": flag}]}
    )
    [source] = search.browser_sources
    assert source.interactive_only is flag
    assert (source.enabled, source.accepted_at, source.text_version) == (False, None, None)
    assert search.interactive_sites == []
    assert search.platform_consents == {}


@pytest.mark.parametrize("kind", ["site", "vie", "google_careers"])
def test_unprotected_kinds_cannot_carry_permission_only_intent(kind: str) -> None:
    with pytest.raises(ValidationError):
        BrowserSource.model_validate({"kind": kind, "query": "Robotics", "interactive_only": True})


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_source_intent_rejects_non_boolean_values(value: Any) -> None:
    with pytest.raises(ValidationError):
        BrowserSource.model_validate(
            {"kind": "linkedin", "query": "Robotics", "interactive_only": value}
        )


@pytest.mark.parametrize("model", [LinkedInIn, PlatformIn])
@pytest.mark.parametrize("value", [False, True])
def test_dedicated_inputs_distinguish_an_omitted_flag_from_explicit_false(
    model: Any, value: bool
) -> None:
    omitted = model.model_validate({"enabled": True})
    assert omitted.interactive_only is False
    assert "interactive_only" not in omitted.model_fields_set
    explicit = model.model_validate({"enabled": True, "interactive_only": value})
    assert explicit.interactive_only is value
    assert "interactive_only" in explicit.model_fields_set


@pytest.mark.parametrize("model", [LinkedInIn, PlatformIn])
@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_dedicated_inputs_keep_strict_boolean_validation(model: Any, value: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"enabled": True, "interactive_only": value})


@pytest.mark.parametrize("model", [LinkedInIn, PlatformIn])
def test_execution_intent_does_not_make_enabled_optional(model: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"interactive_only": True})
