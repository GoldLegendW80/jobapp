"""The unwanted places of the search section (`location_exclude`, owner 2026-10-27, #626): a term
list shaped like `location`, empty for a section saved before it, checked like every term list,
saved, exported and imported with the profile. Its filtering is tested with the sweep
(tests/jobs/test_unwanted_places.py)."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.profile.schemas import TERM_MAX, TERMS_MAX, SearchSection, validate_section
from tests.identity.api_helpers import Api
from tests.profile.conftest import FULL, full, signed_in
from tests.profile.test_api import read, save, save_ok

EMPTY = {"terms": [], "advanced_regex": False}


def search_with(**fields: Any) -> dict[str, Any]:
    data = full("search")
    data.update(fields)
    return data


# --- the field ----------------------------------------------------------------------------------


def test_a_search_section_saved_before_the_field_reads_it_empty() -> None:
    old = {k: v for k, v in full("search").items() if k != "location_exclude"}
    stored = validate_section("search", old)
    assert stored["location_exclude"] == EMPTY
    assert SearchSection.model_validate(old).location_exclude.terms == []
    assert validate_section("search", {})["location_exclude"] == EMPTY


def test_the_field_follows_location_in_the_stored_order() -> None:
    keys = list(validate_section("search", full("search")))
    assert keys[keys.index("location") + 1] == "location_exclude"


@pytest.mark.parametrize(
    ("value", "terms"),
    [
        ({"terms": ["United States"]}, ["United States"]),
        ({"terms": ["Lyon", "lyon", "LYON"]}, ["Lyon"]),  # duplicates ignoring case go
        ({"terms": ["Île-de-France", "São Paulo", "東京"]}, ["Île-de-France", "São Paulo", "東京"]),
        ({"terms": ["on site"], "advanced_regex": False}, ["on site"]),
        ({"terms": [r"\bUS\b", "united (states|kingdom)"], "advanced_regex": True},
         [r"\bUS\b", "united (states|kingdom)"]),
        ({"terms": ["x" * TERM_MAX]}, ["x" * TERM_MAX]),
        ({"terms": [f"place {i}" for i in range(TERMS_MAX)]},
         [f"place {i}" for i in range(TERMS_MAX)]),
        ({}, []),
    ],
)  # fmt: skip
def test_valid_lists_are_kept(value: dict[str, Any], terms: list[str]) -> None:
    stored = validate_section("search", search_with(location_exclude=value))
    assert stored["location_exclude"]["terms"] == terms
    assert stored["location_exclude"]["advanced_regex"] is value.get("advanced_regex", False)


@pytest.mark.parametrize(
    ("value", "where", "message"),
    [
        ({"terms": ["(unclosed"], "advanced_regex": True}, "location_exclude.terms",
         "invalid regular expression"),
        ({"terms": ["(?i)paris"], "advanced_regex": True}, "location_exclude.terms",
         "inline flags"),
        ({"terms": ["(?P<a>x)", "(?P<a>y)"], "advanced_regex": True}, "location_exclude.terms",
         "group name"),
        ({"terms": ["x" * (TERM_MAX + 1)]}, "location_exclude.terms", "at most 200"),
        ({"terms": [f"p{i}" for i in range(TERMS_MAX + 1)]}, "location_exclude.terms",
         "at most 300"),
        ({"terms": ["Paris\nLyon"]}, "location_exclude.terms.0", ""),
        ({"terms": [""]}, "location_exclude.terms.0", ""),
        ({"terms": "Paris"}, "location_exclude.terms", "list"),
        ({"terms": [1]}, "location_exclude.terms.0", "string"),
        ({"terms": [], "advanced_regex": "yes"}, "location_exclude.advanced_regex", "boolean"),
        ({"terms": [], "case": True}, "location_exclude.case", "Extra inputs"),
        (["Paris"], "location_exclude", "valid dictionary"),
        ("Paris", "location_exclude", "valid dictionary"),
        (None, "location_exclude", "valid dictionary"),
    ],
)  # fmt: skip
def test_invalid_lists_are_refused(value: Any, where: str, message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_section("search", search_with(location_exclude=value))
    error = caught.value.errors()[0]
    assert ".".join(str(part) for part in error["loc"]).startswith(where)
    assert message.casefold() in error["msg"].casefold()


# --- through the API -----------------------------------------------------------------------------


@pytest.fixture
def ana(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-places-ana@example.org")


@pytest.fixture
def ben(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, "e2e-places-ben@example.org")


def test_a_save_reads_back_and_a_bad_list_is_a_422_with_its_path(api: Api, ana: str) -> None:
    places = {"terms": ["United States", "Canada"], "advanced_regex": False}
    body = save_ok(api, ana, "search", search_with(location_exclude=places), 0)
    assert body["sections"]["search"]["location_exclude"] == places
    assert read(api, ana)["sections"]["search"]["location_exclude"] == places
    broken = {"terms": ["(unclosed"], "advanced_regex": True}
    response = save(api, ana, "search", search_with(location_exclude=broken), 1)
    assert response.status_code == 422
    assert "location_exclude.terms" in response.json()["detail"]
    assert read(api, ana)["sections"]["search"]["location_exclude"] == places


def test_an_export_with_unwanted_places_round_trips(api: Api, ana: str) -> None:
    for revision, name in enumerate(FULL):
        save_ok(api, ana, name, full(name), revision)
    exported = api.get("/api/profile/export", token=ana).json()
    assert exported["sections"]["search"]["location_exclude"] == FULL["search"]["location_exclude"]
    exported["sections"]["search"]["location_exclude"] = {
        "terms": ["Remote, United States"],
        "advanced_regex": False,
    }
    response = api.post("/api/profile/import", token=ana, json=exported)
    assert response.status_code == 200, response.text
    assert read(api, ana)["sections"]["search"]["location_exclude"]["terms"] == [
        "Remote, United States"
    ]


def test_an_old_export_without_the_field_imports_it_empty(api: Api, ana: str) -> None:
    sections = {name: full(name) for name in FULL}
    sections["search"].pop("location_exclude")
    document = {
        "format": "jobapp-profile",
        "version": 1,
        "exported_at": "2026-10-01T00:00:00Z",
        "sections": sections,
    }
    response = api.post("/api/profile/import", token=ana, json=document)
    assert response.status_code == 200, response.text
    assert read(api, ana)["sections"]["search"]["location_exclude"] == EMPTY


def test_b_never_reads_a_s_unwanted_places(api: Api, ana: str, ben: str) -> None:
    save_ok(api, ana, "search", full("search"), 0)
    assert read(api, ben)["sections"]["search"] is None
    assert read(api, ana)["sections"]["search"]["location_exclude"]["terms"] == ["united states"]
