"""How a ticked LinkedIn is searched (`search.linkedin_search`, owner 2026-10-30: "Both,
switchable in setup"): "api", LinkedIn's public job search, the default, or "browser". Saved with
the search section, kept when an older editor or an import leaves the key out, replaced when sent,
never another person's. The sweep's choice of source is tested in
tests/browser/test_linkedin_guest.py."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.profile.schemas import SearchSection, validate_section
from tests.identity.api_helpers import Api
from tests.profile.conftest import full, signed_in
from tests.profile.test_api import read, save, save_ok


def without_choice(data: dict[str, Any]) -> dict[str, Any]:
    """What the Search tab, an older page or an export from before the field sends."""
    return {k: v for k, v in data.items() if k != "linkedin_search"}


@pytest.fixture
def ana(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-linkedin-mode-ana@example.org")


@pytest.fixture
def ben(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-linkedin-mode-ben@example.org")


def test_the_default_is_the_public_job_search_and_only_two_values_are_taken() -> None:
    old = without_choice(full("search"))
    assert SearchSection.model_validate(old).linkedin_search == "api"
    # Left out of the stored form when omitted, so the service keeps a stored choice.
    assert "linkedin_search" not in validate_section("search", old)
    assert validate_section("search", {"linkedin_search": "browser"})["linkedin_search"] == (
        "browser"
    )
    for wrong in ("API", "headless", "", None, 1, True):
        with pytest.raises(ValidationError, match="linkedin_search"):
            validate_section("search", {"linkedin_search": wrong})


def test_a_save_keeps_a_stored_choice_it_leaves_out_and_replaces_it_when_sent(
    api: Api, ana: str
) -> None:
    body = save_ok(api, ana, "search", full("search") | {"linkedin_search": "browser"}, 0)
    assert body["sections"]["search"]["linkedin_search"] == "browser"
    stored = read(api, ana)["sections"]["search"]
    save_ok(api, ana, "search", without_choice(stored) | {"min_fit": 55}, 1)
    after = read(api, ana)["sections"]["search"]
    assert (after["min_fit"], after["linkedin_search"]) == (55, "browser")
    save_ok(api, ana, "search", stored | {"linkedin_search": "api"}, 2)
    assert read(api, ana)["sections"]["search"]["linkedin_search"] == "api"
    refused = save(api, ana, "search", stored | {"linkedin_search": "robot"}, 3)
    assert refused.status_code == 422
    assert read(api, ana)["sections"]["search"]["linkedin_search"] == "api"


def test_b_never_reads_a_s_choice(api: Api, ana: str, ben: str) -> None:
    save_ok(api, ana, "search", full("search") | {"linkedin_search": "browser"}, 0)
    assert read(api, ben)["sections"]["search"] is None
