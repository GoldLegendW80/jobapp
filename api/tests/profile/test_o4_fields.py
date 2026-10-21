"""The profile fields of opportunities O4: seniority, industries, company types and compensation
in `search`, the new `preferences` section, its completeness, export and import, history, and
the onboarding dates only the wizard writes."""

import copy
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.profile import service
from app.profile.router import ONBOARDING_RESERVED, STALE
from app.profile.schemas import (
    INDUSTRIES_MAX,
    PAY_MAX,
    PreferencesSection,
    SearchSection,
    completeness,
    validate_section,
)
from tests.identity.api_helpers import Api, Clock
from tests.profile.conftest import FULL, full, signed_in
from tests.profile.test_api import read, save, save_ok

PAY = {"min": 500, "max": 650, "currency": "EUR"}


def search_with(**fields: Any) -> dict[str, Any]:
    data = full("search")
    data.update(fields)
    return data


# --- the search fields --------------------------------------------------------------------------


def test_a_search_section_saved_before_o4_reads_the_defaults() -> None:
    old = {k: v for k, v in full("search").items() if k not in ("seniority", "industries")}
    old.pop("company_types")
    old.pop("compensation")
    stored = validate_section("search", old)
    assert stored["seniority"] == []
    assert stored["industries"] == {"terms": [], "advanced_regex": False}
    assert stored["company_types"] == []
    assert stored["compensation"] == {"salary": None, "daily_rate": None}


@pytest.mark.parametrize(
    "fields",
    [
        {"seniority": ["intern", "junior", "mid", "senior", "lead", "principal"]},
        {"seniority": []},
        {"company_types": ["startup", "scaleup", "large_company", "public_sector"]},
        {"company_types": ["nonprofit", "agency", "consulting"]},
        {"industries": {"terms": ["fintech"] * 1 + ["santé"], "advanced_regex": False}},
        {"industries": {"terms": [f"t{i}" for i in range(INDUSTRIES_MAX)]}},
        {"compensation": {"salary": None, "daily_rate": PAY}},
        {"compensation": {"salary": {"min": 0, "max": PAY_MAX, "currency": "USD"}}},
        {"compensation": {"salary": {"min": 60000, "max": 60000, "currency": "CHF"}}},
        {"compensation": {}},
    ],
)
def test_valid_values_are_kept(fields: dict[str, Any]) -> None:
    stored = validate_section("search", search_with(**fields))
    for key, value in fields.items():
        if key == "compensation":
            assert stored[key] == {"salary": None, "daily_rate": None, **value}
        elif key == "industries":
            assert stored[key]["terms"] == value["terms"]
        else:
            assert stored[key] == value


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"seniority": ["senior", "senior"]}, "duplicate seniority"),
        ({"seniority": ["expert"]}, "Input should be"),
        ({"seniority": "senior"}, "valid list"),
        ({"seniority": ["Senior"]}, "Input should be"),
        ({"company_types": ["startup", "startup"]}, "duplicate company type"),
        ({"company_types": ["bank"]}, "Input should be"),
        ({"company_types": [1]}, "Input should be"),
        ({"industries": {"terms": [f"t{i}" for i in range(INDUSTRIES_MAX + 1)]}}, "at most 30"),
        ({"industries": {"terms": [f"t{i}" for i in range(50)]}}, "at most 30"),
        ({"industries": ["fintech"]}, "valid dictionary"),
        ({"industries": {"terms": ["(a+)+"], "advanced_regex": True}}, "nested"),
        ({"compensation": {"salary": {"min": 70, "max": 60, "currency": "EUR"}}}, "below"),
        ({"compensation": {"salary": {"min": -1, "max": 60, "currency": "EUR"}}}, "greater"),
        ({"compensation": {"salary": {"min": 1, "max": PAY_MAX + 1, "currency": "EUR"}}}, "less"),
        ({"compensation": {"daily_rate": {**PAY, "currency": "XYZ"}}}, "unknown currency"),
        ({"compensation": {"daily_rate": {**PAY, "currency": "eur"}}}, "pattern"),
        ({"compensation": {"daily_rate": {**PAY, "currency": "€"}}}, "pattern"),
        ({"compensation": {"daily_rate": {**PAY, "min": "500"}}}, "valid integer"),
        ({"compensation": {"daily_rate": {**PAY, "min": 500.5}}}, "valid integer"),
        ({"compensation": {"daily_rate": {"min": 500, "currency": "EUR"}}}, "required"),
        ({"compensation": {"daily_rate": {**PAY, "period": "day"}}}, "Extra inputs"),
        ({"compensation": {"bonus": None}}, "Extra inputs"),
        ({"compensation": None}, "valid dictionary"),
    ],
)
def test_invalid_values_are_refused(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_section("search", search_with(**fields))
    assert message in str(caught.value)


# --- the preferences section --------------------------------------------------------------------


def test_preferences_defaults_and_values() -> None:
    assert PreferencesSection().model_dump(mode="json") == {
        "onboarding": {"completed_at": None, "dismissed_at": None},
        "documents_language": "auto",
        "documents_tone": "plain",
    }
    stored = validate_section("preferences", {"documents_language": "en"})
    assert stored["documents_tone"] == "plain"
    instant = {"onboarding": {"completed_at": "2026-10-20T10:30:00+02:00"}}
    assert validate_section("preferences", instant)["onboarding"]["completed_at"] == (
        "2026-10-20T08:30:00+00:00"
    )


@pytest.mark.parametrize(
    "data",
    [
        {"documents_language": "de"},
        {"documents_language": "EN"},
        {"documents_tone": "casual"},
        {"documents_tone": None},
        {"onboarding": {"completed_at": "2026-10-20T10:30:00"}},  # no offset
        {"onboarding": {"completed_at": "yesterday"}},
        {"onboarding": {"completed_at": 1760000000}},
        {"onboarding": {"started_at": None}},
        {"theme": "dark"},
    ],
)
def test_invalid_preferences_are_refused(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate_section("preferences", data)


def test_preferences_count_as_filled_and_leave_the_overall_figure_alone() -> None:
    empty = completeness({})
    assert empty.sections["preferences"].percent == 100
    assert empty.sections["preferences"].missing == []
    assert empty.overall == 0
    whole = completeness({name: FULL[name] for name in FULL if name != "preferences"})
    assert whole.overall == completeness(FULL).overall


# --- through the API -----------------------------------------------------------------------------


@pytest.fixture
def ana(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-o4-ana@example.org")


@pytest.fixture
def ben(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-o4-ben@example.org")


def test_each_save_is_one_revision_and_reads_back(api: Api, ana: str) -> None:
    body = save_ok(api, ana, "search", full("search"), 0)
    assert body["sections"]["search"]["compensation"] == FULL["search"]["compensation"]
    body = save_ok(api, ana, "preferences", full("preferences"), 1)
    assert body["revision"] == 2
    assert read(api, ana)["sections"]["preferences"] == FULL["preferences"]
    history = api.get("/api/profile/revisions?section=preferences", token=ana).json()
    assert [item["revision"] for item in history["items"]] == [2]


def test_a_stale_revision_is_409_with_the_current_data(api: Api, ana: str) -> None:
    save_ok(api, ana, "preferences", full("preferences"), 0)
    response = save(api, ana, "preferences", {"documents_tone": "formal"}, 0)
    assert response.status_code == 409
    assert response.json()["detail"] == STALE
    assert response.json()["sections"]["preferences"] == FULL["preferences"]


@pytest.mark.parametrize("field", ["completed_at", "dismissed_at"])
def test_the_person_cannot_write_the_onboarding_dates(api: Api, ana: str, field: str) -> None:
    data = full("preferences")
    data["onboarding"][field] = "2026-10-20T08:00:00+00:00"
    response = save(api, ana, "preferences", data, 0)
    assert response.status_code == 422
    assert ONBOARDING_RESERVED in response.text
    assert read(api, ana)["revision"] == 0


def test_the_dates_the_wizard_recorded_survive_a_save_and_an_import(
    api: Api, ana: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    profile = read(api, ana)
    with factory() as db, db.begin():
        user_id = db.execute(
            sa.select(User.id).where(User.email == "e2e-o4-ana@example.org")
        ).scalar_one()
        service.record_onboarding(db, user_id, "dismissed_at", clock.now)
    loaded = read(api, ana)
    dates = loaded["sections"]["preferences"]["onboarding"]
    assert dates["dismissed_at"] is not None
    assert dates["completed_at"] is None
    # The page sends the section back as loaded: the dates are unchanged, the save goes through.
    data = copy.deepcopy(loaded["sections"]["preferences"])
    data["documents_tone"] = "formal"
    save_ok(api, ana, "preferences", data, loaded["revision"])
    # Clearing them is a change too, and refused.
    cleared = {**data, "onboarding": {"completed_at": None, "dismissed_at": None}}
    assert save(api, ana, "preferences", cleared, loaded["revision"] + 1).status_code == 422
    # An import (here a file with no dates) keeps the stored ones.
    exported = api.get("/api/profile/export", token=ana).json()
    exported["sections"]["preferences"]["onboarding"] = {"completed_at": None, "dismissed_at": None}
    response = api.post("/api/profile/import", token=ana, json=exported)
    assert response.status_code == 200, response.text
    after = read(api, ana)["sections"]["preferences"]
    assert after["onboarding"] == dates
    assert after["documents_tone"] == "formal"
    assert profile["revision"] == 0


def test_an_export_with_the_fields_round_trips(api: Api, ana: str) -> None:
    for revision, name in enumerate(FULL):
        save_ok(api, ana, name, full(name), revision)
    exported = api.get("/api/profile/export", token=ana).json()
    assert exported["sections"]["search"]["seniority"] == ["mid", "senior"]
    assert exported["sections"]["preferences"] == FULL["preferences"]
    response = api.post("/api/profile/import", token=ana, json=exported)
    assert response.status_code == 200, response.text
    assert read(api, ana)["sections"] == FULL


def test_an_old_export_without_the_fields_imports(api: Api, ana: str) -> None:
    sections = {name: full(name) for name in FULL if name != "preferences"}
    for key in ("seniority", "industries", "company_types", "compensation"):
        sections["search"].pop(key)
    document = {
        "format": "jobapp-profile",
        "version": 1,
        "exported_at": "2026-10-01T00:00:00Z",
        "sections": sections,
    }
    response = api.post("/api/profile/import", token=ana, json=document)
    assert response.status_code == 200, response.text
    body = read(api, ana)["sections"]
    assert body["preferences"] == PreferencesSection().model_dump(mode="json")
    assert body["search"]["seniority"] == []
    assert body["search"]["compensation"]["salary"] is None


def test_b_never_reads_a_s_preferences(api: Api, ana: str, ben: str) -> None:
    save_ok(api, ana, "preferences", full("preferences"), 0)
    save_ok(api, ana, "search", full("search"), 1)
    other = read(api, ben)
    assert other["sections"]["preferences"] is None
    assert other["sections"]["search"] is None
    assert api.get("/api/profile/revisions?section=preferences", token=ben).json()["items"] == []
    assert SearchSection.model_validate(read(api, ana)["sections"]["search"]).seniority
